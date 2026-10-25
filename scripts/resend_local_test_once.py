"""Valida y procesa una sola notificacion CU31 en la base local aislada.

Sin ``--send-once`` este comando es estrictamente de solo lectura. La URL de
base de datos se fija antes de importar la aplicacion para impedir que la
configuracion habitual del backend abra una conexion hacia Supabase.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


LOCAL_DATABASE_URL = (
    "postgresql+psycopg://postgres@127.0.0.1:5432/clinica_resend_test"
)
EXPECTED_DATABASE = "clinica_resend_test"
EXPECTED_EMAIL = "haroldresend@gmail.com"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Procesa como maximo un comprobante de la base local CU31",
    )
    parser.add_argument(
        "--send-once",
        action="store_true",
        help="autoriza una unica llamada real a Resend",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    url = make_url(LOCAL_DATABASE_URL)
    if (
        url.host != "127.0.0.1"
        or url.port != 5432
        or url.database != EXPECTED_DATABASE
    ):
        raise RuntimeError("Destino PostgreSQL local inseguro")

    # Debe ocurrir antes de cualquier import de app.*: los modulos de sesion
    # construyen sus engines al importarse. load_dotenv no sobreescribe esta
    # variable y, por tanto, la URL remota de .env nunca se utiliza aqui.
    os.environ["DATABASE_URL"] = LOCAL_DATABASE_URL

    from app.core import config
    from app.modules.gestion_pagos.notificacion_runner import procesar_base
    from app.modules.gestion_pagos.providers.resend_provider import ResendProvider

    email_test = config.obtener_configuracion_resend_pruebas()
    if email_test != EXPECTED_EMAIL:
        raise RuntimeError("EMAIL_TEST no coincide con el destinatario autorizado")

    engine = create_engine(LOCAL_DATABASE_URL, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with engine.connect() as connection:
            database, address, port = connection.execute(text(
                "SELECT current_database(), inet_server_addr()::text, "
                "inet_server_port()"
            )).one()
            if (
                database != EXPECTED_DATABASE
                or address not in {"127.0.0.1", "127.0.0.1/32"}
                or port != 5432
            ):
                raise RuntimeError("La conexion no apunta a PostgreSQL local")

            eligible = connection.execute(text("""
                SELECT id, pago_id, estado, destinatario
                FROM notificacion_pago
                WHERE estado = 'PENDIENTE'
                  AND destinatario = :email
                  AND (proximo_intento IS NULL OR proximo_intento <= CURRENT_TIMESTAMP)
                  AND intentos < 8
                ORDER BY id
            """), {"email": EXPECTED_EMAIL}).mappings().all()
            total_notifications = connection.execute(text(
                "SELECT count(*) FROM notificacion_pago"
            )).scalar_one()

        if total_notifications != 1 or len(eligible) != 1:
            raise RuntimeError(
                "Se esperaba exactamente una notificacion total y elegible "
                f"(total={total_notifications}, elegibles={len(eligible)})"
            )

        item = eligible[0]
        print(
            "Entorno local listo: "
            f"base={EXPECTED_DATABASE}, pago_id={item['pago_id']}, "
            f"notificacion_id={item['id']}, estado={item['estado']}"
        )
        if not args.send_once:
            print("Solo verificacion: no se contacto a Resend")
            return 0

        provider = ResendProvider()
        provider.validar_configuracion()
        processed = procesar_base(factory, provider, limite=1)
        if processed != 1:
            raise RuntimeError(
                f"Se esperaba procesar una notificacion; procesadas={processed}"
            )
        print("Una notificacion fue procesada; consulte su estado en la outbox")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
