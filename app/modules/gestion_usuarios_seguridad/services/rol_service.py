from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    RolCrearConPermisos,
    RolActualizarConPermisos,
    ModuloConFuncionesRespuesta,
    FuncionRespuesta,
    PermisoRolRespuesta,
)


def _validar_permisos(
    db: Session,
    permisos: list,
) -> list[tuple[int, int]]:
    """Valida funciones/acciones y devuelve pares (funcion_id, accion_id) sin duplicados."""
    if not permisos:
        return []

    vistos: dict[int, int] = {}

    for permiso in permisos:
        if permiso.funcion_id in vistos:
            raise HTTPException(
                status_code=400,
                detail=(
                    "La función "
                    f"{permiso.funcion_id} está repetida; "
                    "cada función admite una sola acción/nivel de acceso"
                ),
            )

        funcion = repo.obtener_funcion_por_id(db, permiso.funcion_id)
        if not funcion or not funcion.estado:
            raise HTTPException(
                status_code=400,
                detail=f"La función {permiso.funcion_id} no existe o está inactiva",
            )

        accion = repo.obtener_accion_por_id(db, permiso.accion_id)
        if not accion or not accion.estado:
            raise HTTPException(
                status_code=400,
                detail=f"La acción {permiso.accion_id} no existe o está inactiva",
            )

        vistos[permiso.funcion_id] = permiso.accion_id

    return list(vistos.items())


def listar_roles(db: Session):
    return repo.listar_roles(db)


def obtener_rol(
    db: Session,
    rol_id: int,
):
    rol = repo.obtener_rol_por_id(db, rol_id)

    if not rol:
        raise HTTPException(
            status_code=404,
            detail="Rol no encontrado",
        )

    return rol


def crear_rol_con_permisos(
    db: Session,
    datos: RolCrearConPermisos,
):
    existente = repo.obtener_rol_por_nombre(db, datos.nombre)

    if existente:
        raise HTTPException(
            status_code=409,
            detail="Ya existe un rol con ese nombre",
        )

    permisos = _validar_permisos(db, datos.permisos)

    try:
        rol = repo.crear_rol(
            db=db,
            nombre=datos.nombre,
            descripcion=datos.descripcion,
            estado=datos.estado,
        )

        for funcion_id, accion_id in permisos:
            repo.asignar_permiso(
                db=db,
                rol_id=rol.id,
                funcion_id=funcion_id,
                accion_id=accion_id,
            )

        repo.registrar_bitacora(
            db=db,
            # TODO: sustituir por el usuario autenticado una vez integrado JWT.
            usuario_id=None,
            accion="CREAR_ROL",
            entidad_afectada="rol",
            id_registro_afectado=rol.id,
            descripcion=(
                f"Rol '{datos.nombre}' creado "
                f"con {len(permisos)} permiso(s)"
            ),
        )

        db.commit()
        db.refresh(rol)

        return rol

    except Exception:
        db.rollback()
        raise


def actualizar_rol_con_permisos(
    db: Session,
    rol_id: int,
    datos: RolActualizarConPermisos,
):
    rol = obtener_rol(db, rol_id)

    if datos.estado is False and rol.protegido:
        raise HTTPException(
            status_code=400,
            detail="El rol está protegido y no puede desactivarse",
        )

    if (
        datos.nombre is not None
        and datos.nombre != rol.nombre
    ):
        existente = repo.obtener_rol_por_nombre(db, datos.nombre)

        if existente and existente.id != rol_id:
            raise HTTPException(
                status_code=409,
                detail="Ya existe un rol con ese nombre",
            )

    permisos = (
        _validar_permisos(db, datos.permisos)
        if datos.permisos is not None
        else None
    )

    try:
        hay_cambios_datos = (
            datos.nombre is not None
            or datos.descripcion is not None
            or datos.estado is not None
        )

        if hay_cambios_datos:
            repo.actualizar_rol(
                db=db,
                rol=rol,
                nombre=datos.nombre,
                descripcion=datos.descripcion,
                estado=datos.estado,
            )

            repo.registrar_bitacora(
                db=db,
                # TODO: sustituir por el usuario autenticado una vez integrado JWT.
                usuario_id=None,
                accion="ACTUALIZAR_ROL",
                entidad_afectada="rol",
                id_registro_afectado=rol.id,
                descripcion="Datos del rol actualizados",
            )

        if permisos is not None:
            repo.eliminar_permisos_rol(db, rol_id)

            for funcion_id, accion_id in permisos:
                repo.asignar_permiso(
                    db=db,
                    rol_id=rol_id,
                    funcion_id=funcion_id,
                    accion_id=accion_id,
                )

            repo.registrar_bitacora(
                db=db,
                # TODO: sustituir por el usuario autenticado una vez integrado JWT.
                usuario_id=None,
                accion="ACTUALIZAR_PERMISOS_ROL",
                entidad_afectada="rol",
                id_registro_afectado=rol.id,
                descripcion=(
                    f"Permisos del rol reemplazados "
                    f"({len(permisos)} permiso(s))"
                ),
            )

        db.commit()
        db.refresh(rol)

        return rol

    except Exception:
        db.rollback()
        raise


def desactivar_rol(
    db: Session,
    rol_id: int,
):
    rol = obtener_rol(db, rol_id)

    if rol.protegido:
        raise HTTPException(
            status_code=400,
            detail="El rol está protegido y no puede desactivarse",
        )

    if not rol.estado:
        raise HTTPException(
            status_code=409,
            detail="El rol ya está desactivado",
        )

    try:
        rol = repo.desactivar_rol(db, rol)

        repo.registrar_bitacora(
            db=db,
            # TODO: sustituir por el usuario autenticado una vez integrado JWT.
            usuario_id=None,
            accion="ELIMINAR_ROL",
            entidad_afectada="rol",
            id_registro_afectado=rol.id,
            descripcion="Rol desactivado",
        )

        db.commit()

        return rol

    except Exception:
        db.rollback()
        raise


def obtener_permisos_rol(
    db: Session,
    rol_id: int,
) -> list[PermisoRolRespuesta]:
    obtener_rol(db, rol_id)

    filas = repo.obtener_permisos_rol(db, rol_id)

    return [
        PermisoRolRespuesta(
            rol_id=fila["rol_id"],
            funcion_id=fila["funcion_id"],
            funcion_nombre=fila["funcion_nombre"],
            modulo_id=fila["modulo_id"],
            modulo_nombre=fila["modulo_nombre"],
            accion_id=fila["accion_id"],
            accion_nombre=fila["accion_nombre"],
        )
        for fila in filas
    ]


def listar_modulos_con_funciones(
    db: Session,
) -> list[ModuloConFuncionesRespuesta]:
    filas = repo.listar_modulos_con_funciones(db)

    modulos: dict[int, ModuloConFuncionesRespuesta] = {}

    for fila in filas:
        modulo_id = fila["modulo_id"]

        if modulo_id not in modulos:
            modulos[modulo_id] = ModuloConFuncionesRespuesta(
                id=modulo_id,
                nombre=fila["modulo_nombre"],
                funciones=[],
            )

        if fila["funcion_id"] is not None:
            modulos[modulo_id].funciones.append(
                FuncionRespuesta(
                    id=fila["funcion_id"],
                    modulo_id=modulo_id,
                    nombre=fila["funcion_nombre"],
                    descripcion=fila["funcion_descripcion"],
                    estado=True,
                )
            )

    return list(modulos.values())


def listar_acciones(db: Session):
    return repo.listar_acciones(db)
