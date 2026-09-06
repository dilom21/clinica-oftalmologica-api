from sqlalchemy.orm import Session

from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    MenuModuloRespuesta,
    MenuFuncionRespuesta,
)


def obtener_menu(
    db: Session,
    rol_id: int,
) -> list[MenuModuloRespuesta]:
    """Menú dinámico del rol a partir de `rol_funcion`.

    Devuelve únicamente los módulos con al menos una función asignada al
    rol y cada función con la acción otorgada en `rol_funcion`/`accion`.
    Se ignoran módulos, funciones y acciones inactivas.
    """
    filas = repo.listar_menu_por_rol(db, rol_id)

    modulos: dict[int, MenuModuloRespuesta] = {}

    for fila in filas:
        modulo_id = fila["modulo_id"]

        if modulo_id not in modulos:
            modulos[modulo_id] = MenuModuloRespuesta(
                id=modulo_id,
                nombre=fila["modulo_nombre"],
                funciones=[],
            )

        modulos[modulo_id].funciones.append(
            MenuFuncionRespuesta(
                id=fila["funcion_id"],
                nombre=fila["funcion_nombre"],
                accion_id=fila["accion_id"],
                accion_nombre=fila["accion_nombre"],
            )
        )

    return list(modulos.values())
