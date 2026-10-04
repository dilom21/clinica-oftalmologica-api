r"""Aplica únicamente database/cu19_controles_medicos.sql a la BD configurada.

Uso: .\venv\Scripts\python.exe scripts\aplicar_sql_cu19.py --aplicar
Sin --aplicar solo verifica los requisitos del esquema mediante lecturas.
No importa la aplicación ni requiere credenciales Gmail.
"""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    raiz = Path(__file__).resolve().parents[1]
    load_dotenv(raiz / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL no está configurada")
    engine = None
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 10})
        with engine.connect() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            columnas = {c["name"] for c in inspect(db).get_columns("control_medico", schema="public")}
            necesarias = {
                "id", "consulta_clinica_id", "paciente_id", "oftalmologo_id",
                "fecha_programada", "motivo", "estado",
            }
            if not necesarias.issubset(columnas):
                raise RuntimeError("El esquema control_medico no coincide con el inspeccionado para CU19")
            print("Tabla control_medico existente: requisitos verificados.")
        if args.aplicar:
            # El archivo SQL delimita su propia transacción BEGIN/COMMIT.
            sql = (raiz / "database" / "cu19_controles_medicos.sql").read_text(encoding="utf-8")
            with engine.connect().execution_options(
                isolation_level="AUTOCOMMIT", no_parameters=True,
            ) as db:
                db.exec_driver_sql(sql)
            print("SQL de CU19 aplicado.")
        with engine.connect() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            columnas = {c["name"] for c in inspect(db).get_columns("control_medico", schema="public")}
            permisos = db.execute(text("""
                SELECT r.nombre AS rol, f.nombre AS funcion, a.nombre AS accion,
                       r.estado AS rol_activo, f.estado AS funcion_activa,
                       a.estado AS accion_activa
                FROM public.rol_funcion rf
                JOIN public.rol r ON r.id = rf.rol_id
                JOIN public.funcion f ON f.id = rf.funcion_id
                JOIN public.accion a ON a.id = rf.accion_id
                WHERE f.nombre = :nombre
            """), {"nombre": "Programar controles médicos"}).mappings().all()
            print(json.dumps({
                "observaciones_disponible": "observaciones" in columnas,
                "permisos_cu19": [dict(p) for p in permisos],
            }, ensure_ascii=True))
        return 0
    except Exception as exc:
        # No imprimir DSN, credenciales ni datos clínicos ante un fallo.
        print(f"No se pudo completar la operación CU19 ({type(exc).__name__}).")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
