"""Interactively create one SaaS control-plane administrator."""
from getpass import getpass
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.security import hash_password
from app.database.session import SessionLocal
from app.modules.administracion_saas.models import SaasUsuario
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


def main() -> int:
    correo = input("Correo SaaS: ").strip().casefold()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", correo):
        print("Correo inválido")
        return 2
    password = getpass("Contraseña: ")
    confirmacion = getpass("Confirmar contraseña: ")
    if not password or password != confirmacion:
        print("Las contraseñas no coinciden")
        return 2
    db = SessionLocal()
    try:
        if db.query(SaasUsuario).filter(SaasUsuario.correo == correo).first():
            print("El usuario ya existe")
            return 1
        db.add(SaasUsuario(correo=correo, password_hash=hash_password(password),
                           nombres=input("Nombres: ").strip(), apellidos=input("Apellidos: ").strip(),
                           rol="SUPERADMIN", estado=True))
        db.commit()
        print("Usuario SaaS creado")
        return 0
    except IntegrityError:
        db.rollback()
        print("No se pudo crear el usuario SaaS: el correo ya está registrado.")
        return 1
    except SQLAlchemyError:
        db.rollback()
        print("No se pudo crear el usuario SaaS por un error de base de datos.")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
