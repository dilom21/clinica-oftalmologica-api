"""Owner policy M-4: physical database names are SUPERADMIN-only.

OPERADOR keeps general SaaS read access, but no payload may expose the physical
name of a canonical tenant database (``tenant_*``), a restore temporary
(``restore_*``), a retained backup (``restore_backup_*``) or a rehearsal probe
(``restore_probe_*``). Tenant JWTs never reach these endpoints; the SaaS bearer
dependency rejects them with 401.

The redaction lives in this module so listings, details, audit logs and any
future serializer share exactly one policy implementation.
"""

from __future__ import annotations

import re

# Physical names travel both as standalone values and inside free-text fields
# (``restore_tenant.mensaje_error`` and ``saas_bitacora.descripcion``).
# ``restore_id``/``tenant_database_id`` are correlation labels, not names, and
# must survive redaction.
PHYSICAL_NAME_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:restore_backup_[A-Za-z0-9_]+"
    r"|restore_probe_[A-Za-z0-9_]+"
    r"|restore_(?!id\b)[A-Za-z0-9_]+"
    r"|tenant_(?!database_id\b)[a-z0-9_]+)"
    r"(?![A-Za-z0-9_])"
)

# Retention note fields that carry a physical name (see restore_service
# ``_retention_note``). They are removed as a whole so the label never reveals
# that a name was omitted, and the remaining correlation keys stay readable.
SENSITIVE_NOTE_FIELDS = (
    "base_canonica",
    "base_temporal",
    "base_temporal_promovida",
    "base_respaldo",
)

REDACTED_TEXT = "[NOMBRE_BASE_REDACTADO]"


def puede_ver_nombres_fisicos(usuario) -> bool:
    """Only SUPERADMIN may see physical database names (owner decision M-4)."""
    return getattr(usuario, "rol", None) == "SUPERADMIN"


def nombre_fisico(usuario, valor: str | None) -> str | None:
    """Return the physical name only for SUPERADMIN; otherwise ``None``."""
    if not puede_ver_nombres_fisicos(usuario):
        return None
    return valor


def _sin_campos_de_nombres(texto: str) -> str:
    partes = []
    for segmento in str(texto).split(";"):
        clave = segmento.split("=", 1)[0].strip()
        if clave in SENSITIVE_NOTE_FIELDS:
            continue
        partes.append(segmento)
    return ";".join(partes).replace("; ;", "; ").strip("; ")


def redactar_nombres_fisicos(texto: str | None) -> str | None:
    """Remove every physical database name from free text (fail closed)."""
    if not texto:
        return texto
    limpio = _sin_campos_de_nombres(str(texto))
    return PHYSICAL_NAME_PATTERN.sub(REDACTED_TEXT, limpio)
