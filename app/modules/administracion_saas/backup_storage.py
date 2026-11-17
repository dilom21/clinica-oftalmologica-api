"""Private backup storage contract and local development provider."""

import enum
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator, Protocol


# The directory/file path is passed only through the child environment, not argv.
# Raw DACL inspection includes inherited ACEs; an allow for any other SID (or any
# deny/unknown ACE) is rejected rather than trying to infer effective rights.
#
# The inspector runs under an explicit policy:
#   ROOT  - the configured private storage root. A protected DACL is mandatory.
#           The root itself, its owner and every ACE are verified.
#   CHILD - an object created inside an already verified ROOT. NTFS keeps
#           inherited ACEs in the child descriptor, so a child DACL is normally
#           *unprotected* (AreAccessRulesProtected == False) while its effective
#           ACL is still exclusively private. Requiring protection on children
#           rejected legitimate staging directories, dumps, `.partial` files and
#           the final stored object. CHILD therefore accepts an inherited DACL,
#           but only after re-checking the complete effective ACL: allowed owner,
#           allow-only ACEs, known SIDs only, service inheritable full control,
#           no Everyone/Users/Authenticated Users, no unknown/unverifiable ACE,
#           no reparse point. Any unverifiable condition still fails closed.
# SAAS_BACKUP_ACL_REQUIRE_PROTECTED is fail-closed: only an explicit '0' relaxes
# the protected-DACL requirement, so a missing value is treated as ROOT-strict.
_WINDOWS_ACL_CHECK = r"""
$ErrorActionPreference = 'Stop'
try {
    $path = $env:SAAS_BACKUP_ACL_TARGET
    $directory = $env:SAAS_BACKUP_ACL_DIRECTORY -eq '1'
    $requireProtected = -not ($env:SAAS_BACKUP_ACL_REQUIRE_PROTECTED -eq '0')
    if ([string]::IsNullOrWhiteSpace($path)) { throw 'Missing path' }
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if ((([int]$item.Attributes -band [int][IO.FileAttributes]::ReparsePoint)) -ne 0) { throw 'Reparse point' }
    if ($item.PSIsContainer -ne $directory) { throw 'Incorrect object type' }
    $service = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $allowed = @($service, 'S-1-5-18', 'S-1-5-32-544')
    $acl = Get-Acl -LiteralPath $path -ErrorAction Stop
    if ($requireProtected -and -not $acl.AreAccessRulesProtected) { throw 'DACL inheritance is enabled' }
    $raw = [Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
    if ($null -eq $raw.DiscretionaryAcl -or $raw.DiscretionaryAcl.Count -eq 0) { throw 'Missing DACL' }
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $allowed) { throw 'Unknown owner' }
    $serviceFull = $false
    foreach ($ace in $raw.DiscretionaryAcl) {
        if ($ace -isnot [Security.AccessControl.CommonAce] -or
            $ace.AceType -ne [Security.AccessControl.AceType]::AccessAllowed -or
            $ace.SecurityIdentifier.Value -notin $allowed) { throw 'Unsafe or unknown ACE' }
        if ($ace.SecurityIdentifier.Value -eq $service -and
            (([int]$ace.AccessMask -band [int]0x1f01ff) -eq 0x1f01ff)) {
            if (-not $directory -or
                ((([int]$ace.AceFlags -band [int][Security.AccessControl.AceFlags]::ContainerInherit) -ne 0) -and
                 (([int]$ace.AceFlags -band [int][Security.AccessControl.AceFlags]::ObjectInherit) -ne 0) -and
                 (([int]$ace.AceFlags -band [int][Security.AccessControl.AceFlags]::InheritOnly) -eq 0))) {
                $serviceFull = $true
            }
        }
    }
    if (-not $serviceFull) { throw 'Service identity lacks inheritable full control' }
    [Console]::Out.Write('PRIVATE_ACL_OK')
} catch {
    exit 7
}
"""


class AclPolicy(enum.Enum):
    """Explicit context for a Windows private ACL inspection.

    ROOT  - configured private storage root: a protected DACL is mandatory.
    CHILD - object created inside an already verified ROOT: an inherited
            (unprotected) DACL is allowed only while the effective ACL stays
            exclusively private.
    """

    ROOT = "root"
    CHILD = "child"

    @property
    def require_protected_dacl(self) -> bool:
        return self is AclPolicy.ROOT


def _powershell_path() -> Path | None:
    # Never rely on a PATH override for the security decision.
    candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    return candidate if candidate.is_file() else None


def _write_windows_acl_script() -> Path:
    # Windows PowerShell 5.1 does not reliably execute a multi-line try/catch block
    # piped through `-Command -`. Materialize the script and run it with `-File`.
    # The script contains no secrets; only the target path travels via the child env.
    descriptor, name = tempfile.mkstemp(prefix="saas-acl-", suffix=".ps1")
    script = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_WINDOWS_ACL_CHECK)
    except Exception:
        script.unlink(missing_ok=True)
        raise
    return script


def _run_windows_acl_check(executable: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = _write_windows_acl_script()
    try:
        return subprocess.run(
            [
                str(executable),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            text=True, capture_output=True, env=env, timeout=15, check=False,
        )
    finally:
        script.unlink(missing_ok=True)


def _inspect_windows_acl(path: Path, *, policy: AclPolicy, directory: bool) -> None:
    executable = _powershell_path()
    if executable is None:
        raise RuntimeError("Windows private ACL inspection is unavailable")
    # Do not forward application/database secrets to the ACL inspector.
    env = {
        key: value for key, value in os.environ.items()
        if key.casefold() in {
            "systemroot", "windir", "comspec", "psmodulepath", "temp", "tmp",
        }
    }
    env["SAAS_BACKUP_ACL_TARGET"] = str(path)
    env["SAAS_BACKUP_ACL_DIRECTORY"] = "1" if directory else "0"
    env["SAAS_BACKUP_ACL_REQUIRE_PROTECTED"] = "1" if policy.require_protected_dacl else "0"
    try:
        checked = _run_windows_acl_check(executable, env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Windows private ACL inspection failed") from exc
    if checked.returncode != 0 or checked.stdout.strip() != "PRIVATE_ACL_OK":
        raise ValueError("Backup path ACL is not private or cannot be verified")


class BackupStorage(Protocol):
    def put(self, key: str, source: Path) -> None: ...
    def get(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def staging(self) -> Iterator[Path]: ...
    def verify_private_archive(self, path: Path) -> None: ...


def _is_windows() -> bool:
    return os.name == "nt"


class LocalPrivateBackupStorage:
    """Explicit private root; never serves files via a web or public bucket."""

    def __init__(self, root: Path):
        if root.is_symlink():
            raise ValueError("Symlink backup root is forbidden")
        self.root = root.resolve(strict=True)
        if not self.root.is_dir() or self.root.is_symlink():
            raise ValueError("Backup storage root must be a private directory")
        if _is_windows():
            _inspect_windows_acl(self.root, policy=AclPolicy.ROOT, directory=True)
        elif self.root.stat().st_mode & 0o077:
            raise ValueError("Backup storage root must not be accessible by other users")

    def _verify_root(self) -> None:
        if _is_windows():
            _inspect_windows_acl(self.root, policy=AclPolicy.ROOT, directory=True)
        elif self.root.stat().st_mode & 0o077:
            raise ValueError("Backup storage root is no longer private")

    def _resolve_inside_root(self, path: Path) -> Path:
        """Resolve a child canonically and prove it stays inside the verified root."""
        if path.is_symlink():
            raise ValueError("Symlink backup path is forbidden")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError("Backup path cannot be resolved safely") from exc
        normalized_root = os.path.normcase(str(self.root))
        parents = {os.path.normcase(str(parent)) for parent in resolved.parents}
        if normalized_root not in parents:
            raise ValueError("Backup path escapes the private root")
        return resolved

    def _verify_child(self, path: Path, *, directory: bool) -> None:
        """Boundary + effective-ACL check for an object inside the verified root."""
        resolved = self._resolve_inside_root(path)
        if _is_windows():
            _inspect_windows_acl(resolved, policy=AclPolicy.CHILD, directory=directory)

    @contextmanager
    def staging(self) -> Iterator[Path]:
        self._verify_root()
        directory = self._create_staging_directory()
        try:
            self._verify_child(directory, directory=True)
            yield directory
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def _create_staging_directory(self) -> Path:
        # On Windows a plain mkdir inherits the private root DACL. tempfile's
        # 0o700 descriptor instead produces a *protected* child with an OWNER
        # RIGHTS ACE that drops the service identity, which the strict CHILD
        # policy must reject. POSIX keeps the restrictive 0o700 mode.
        for _ in range(100):
            candidate = self.root / f"saas-backup-{uuid.uuid4().hex}"
            try:
                if _is_windows():
                    os.mkdir(candidate)
                else:
                    os.mkdir(candidate, 0o700)
            except FileExistsError:
                continue
            return candidate
        raise RuntimeError("Backup staging directory could not be created")

    def verify_private_archive(self, path: Path) -> None:
        if not path.is_file() or path.is_symlink():
            raise ValueError("Backup staging archive is not in the private root")
        self._verify_root()
        self._verify_child(path, directory=False)

    def _path(self, key: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}\.dump", key):
            raise ValueError("Invalid backup storage key")
        self._verify_root()
        path = self.root / key
        if path.is_symlink():
            raise ValueError("Symlink backup path is forbidden")
        return path

    def put(self, key: str, source: Path) -> None:
        self.verify_private_archive(source)
        destination = self._path(key)
        if destination.exists():
            raise FileExistsError("Backup key already exists")
        temporary = self.root / f".{uuid.uuid4().hex}.partial"
        try:
            with temporary.open("xb") as target, source.open("rb") as data:
                if not _is_windows():
                    os.fchmod(target.fileno(), 0o600)
                shutil.copyfileobj(data, target)
                target.flush()
                os.fsync(target.fileno())
            self._verify_child(temporary, directory=False)
            self._verify_root()
            os.replace(temporary, destination)
            self._verify_child(destination, directory=False)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, key: str) -> BinaryIO:
        path = self._path(key)
        if _is_windows():
            self._verify_child(path, directory=False)
        return path.open("rb")

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        path = self._path(key)
        found = path.is_file()
        if found and _is_windows():
            self._verify_child(path, directory=False)
        return found
