"""Full tenant custom-format dump and read-only archive verification."""

import os
import shutil
from pathlib import Path

from scripts.saas.provision_tenant import PostgresConnection, SubprocessRunner, validate_database_identifier


def _is_windows() -> bool:
    return os.name == "nt"


def _minimal_client_environment() -> dict[str, str]:
    """Return only OS runtime values required by explicitly addressed clients."""
    allowed = (
        {"systemroot", "windir", "comspec", "temp", "tmp"}
        if _is_windows()
        else {"lang", "lc_all", "lc_ctype", "tmpdir", "temp", "tmp"}
    )
    return {
        key: value for key, value in os.environ.items()
        if key.casefold() in allowed
    }


def _client_path(name: str, pg_bin_dir: str | None) -> Path | None:
    if pg_bin_dir is not None:
        directory = Path(pg_bin_dir)
        candidate = directory / (f"{name}.exe" if _is_windows() else name)
        if not candidate.is_file() or (not _is_windows() and not os.access(candidate, os.X_OK)):
            return None
        return candidate
    # Keep provisioner's Windows defaults while allowing portable PATH lookup.
    if _is_windows():
        from scripts.saas.provision_tenant import PathToolRunner
        return PathToolRunner().path(name)
    found = shutil.which(name)
    return Path(found) if found else None


class TenantBackupRunner:
    def __init__(self, connection: PostgresConnection, pg_bin_dir: str | None = None,
                 timeout_seconds: int = 900, runner=None):
        if timeout_seconds <= 0:
            raise ValueError("Backup timeout must be positive")
        self.pg_dump = _client_path("pg_dump", pg_bin_dir)
        self.pg_restore = _client_path("pg_restore", pg_bin_dir)
        if self.pg_dump is None or self.pg_restore is None:
            raise RuntimeError("PostgreSQL backup clients are unavailable")
        self.connection = connection
        self.timeout_seconds = timeout_seconds
        self.runner = runner or SubprocessRunner()

    def _env(self) -> dict[str, str]:
        env = _minimal_client_environment()
        env["PGPASSWORD"] = self.connection.password
        env["PGSSLMODE"] = self.connection.sslmode
        return env

    def dump(self, database_name: str, destination: Path) -> None:
        validate_database_identifier(database_name)
        self.runner.run([
            str(self.pg_dump), "--format=custom", "--no-owner", "--no-privileges",
            "--schema=public",
            "-h", self.connection.host, "-p", str(self.connection.port),
            "-U", self.connection.user, "-d", database_name, "-f", str(destination),
        ], self._env(), self.timeout_seconds)

    def verify(self, archive: Path) -> None:
        self.runner.run([str(self.pg_restore), "--list", str(archive)],
                        self._env(), self.timeout_seconds)
