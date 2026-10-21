r"""Ejecuta pruebas locales sin credenciales ni conexiones a PostgreSQL.

Uso: .\venv\Scripts\python.exe scripts\probar_cu19.py [argumentos de pytest]
Sin argumentos ejecuta las pruebas de CU19. Para regresión: scripts\probar_cu19.py tests -q.
"""

import os
from pathlib import Path
import sys


def main() -> int:
    raiz = Path(__file__).resolve().parents[1]
    os.chdir(raiz)
    sys.path.insert(0, str(raiz))
    # Solo afecta a este proceso; no modifica .env ni la configuración del sistema.
    os.environ.update({
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "JWT_SECRET_KEY": "cu19-pruebas-locales-clave-no-productiva-de-32-caracteres",
        "JWT_ALGORITHM": "HS256",
        "PASSWORD_RESET_URL_BASE": "http://localhost/pruebas",
        "GMAIL_CLIENT_ID": "pruebas-locales",
        "GMAIL_CLIENT_SECRET": "pruebas-locales",
        "GMAIL_REFRESH_TOKEN": "pruebas-locales",
        "GMAIL_SENDER_EMAIL": "pruebas@example.test",
    })
    import pytest

    return pytest.main(sys.argv[1:] or ["tests/test_cu19_controles_medicos.py", "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
