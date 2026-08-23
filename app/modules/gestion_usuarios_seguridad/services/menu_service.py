from sqlalchemy.orm import Session

from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    MenuModuloRespuesta,
    MenuFuncionRespuesta,
)


def obtener_menu(db: Session) -> list[MenuModuloRespuesta]:
    modulos = repo.listar_modulos_activos(db)
    funciones = repo.listar_funciones_activas(db)

    menu: list[MenuModuloRespuesta] = []

    for modulo in modulos:
        funciones_modulo = [
            MenuFuncionRespuesta(
                id=funcion.id,
                nombre=funcion.nombre,
            )
            for funcion in funciones
            if funcion.modulo_id == modulo.id
        ]

        menu.append(
            MenuModuloRespuesta(
                id=modulo.id,
                nombre=modulo.nombre,
                funciones=funciones_modulo,
            )
        )

    return menu
