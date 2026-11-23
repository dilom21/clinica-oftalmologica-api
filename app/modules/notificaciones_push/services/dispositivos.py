"""Servicio de dispositivos push y reclamacion global de tokens.

Protocolo recuperable (dos bases fisicas, sin transaccion ACID comun):

1. reservar la huella en Control Plane como ``PENDIENTE`` y confirmar el
   ``commit`` de esa reserva;
2. persistir/actualizar el dispositivo en la base tenant y confirmar;
3. marcar la huella como ``ACTIVO`` en Control Plane.

Si el paso 2 falla, se compensa la reserva creada por *esta* operacion
(``REVOCADO``); nunca se libera una propiedad ``ACTIVO`` previa. Si el paso 3
falla, el dispositivo queda persistido y la huella ``PENDIENTE``: un reintento
del mismo usuario la confirma, y una reserva abandonada queda retenida
(fail-closed) hasta reconciliacion manual. No se implementa worker.

El token FCM y la huella no se registran en logs ni se incluyen en errores.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import (
    DispositivoPush,
    DispositivoPushRegistry,
)
from app.modules.notificaciones_push.repositories import (
    dispositivos as repo_dispositivos,
    registro_global as repo_registro,
)
from app.modules.notificaciones_push.schemas.http import (
    RegistroDispositivoRequest,
)
from app.modules.notificaciones_push.schemas.schemas import (
    DispositivoPushPersistencia,
)
from app.modules.notificaciones_push.seguridad import (
    TokenFcmInvalido,
    calcular_huella_token,
)

MENSAJE_TOKEN_INVALIDO = "Token FCM inválido"
MENSAJE_CONFLICTO = "El dispositivo no puede registrarse"
MENSAJE_REGISTRO_NO_DISPONIBLE = (
    "No se pudo completar el registro del dispositivo"
)
MENSAJE_REVOCACION_NO_DISPONIBLE = (
    "No se pudo completar la revocación del dispositivo"
)
MENSAJE_RECURSO_NO_ENCONTRADO = "Recurso no encontrado"


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _token_valido(token_fcm: str) -> str:
    token = token_fcm.strip() if isinstance(token_fcm, str) else ""
    try:
        calcular_huella_token(token)
    except TokenFcmInvalido:
        # Mensaje generico: no se refleja el valor recibido.
        raise HTTPException(
            status_code=422,
            detail=MENSAJE_TOKEN_INVALIDO,
        ) from None
    return token


def _texto_opcional(valor: str | None) -> str | None:
    if valor is None:
        return None
    limpio = valor.strip()
    return limpio or None


def _datos_persistencia(
    datos: RegistroDispositivoRequest,
    *,
    usuario_id: int,
    token: str,
) -> DispositivoPushPersistencia:
    return DispositivoPushPersistencia(
        usuario_id=usuario_id,
        token_fcm=token,
        plataforma=datos.plataforma,
        instalacion_id=_texto_opcional(datos.instalacion_id),
        app_version=_texto_opcional(datos.app_version),
        nombre_dispositivo=_texto_opcional(datos.nombre_dispositivo),
    )


# --------------------------------------------------------------------------- #
# Protocolo de reclamacion global
# --------------------------------------------------------------------------- #
def _reservar_huella(
    control: Session,
    *,
    token: str,
    tenant_database_id: int,
    usuario_id: int,
    ahora: datetime,
) -> tuple[DispositivoPushRegistry, bool]:
    """Reserva ``PENDIENTE`` de forma atomica. Devuelve (registro, es_nueva)."""
    huella = calcular_huella_token(token)

    # Dos intentos: el primero puede perder una carrera contra otra operacion
    # concurrente; el segundo relee el resultado ya persistido.
    for _ in range(2):
        try:
            existente = repo_registro.obtener_por_huella(control, huella)
        except SQLAlchemyError:
            control.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=MENSAJE_REGISTRO_NO_DISPONIBLE,
            ) from None

        if existente is None:
            try:
                registro = repo_registro.crear_pendiente(
                    control,
                    token_fingerprint=huella,
                    tenant_database_id=tenant_database_id,
                    usuario_id=usuario_id,
                )
                control.commit()
                return registro, True
            except IntegrityError:
                # Otra operacion gano la carrera por la misma huella.
                control.rollback()
                continue
            except SQLAlchemyError:
                control.rollback()
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=MENSAJE_REGISTRO_NO_DISPONIBLE,
                ) from None

        misma_propiedad = (
            existente.tenant_database_id == tenant_database_id
            and existente.usuario_id == usuario_id
        )
        if not misma_propiedad:
            # Token activo/reservado por otro tenant u otro usuario: nunca se
            # roba ni se reasigna mediante un POST.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=MENSAJE_CONFLICTO,
            )

        if existente.estado in ("PENDIENTE", "ACTIVO"):
            return existente, False

        if existente.estado == "REVOCADO":
            try:
                repo_registro.marcar_pendiente(control, existente, ahora)
                control.commit()
                return existente, False
            except SQLAlchemyError:
                control.rollback()
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=MENSAJE_REGISTRO_NO_DISPONIBLE,
                ) from None

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=MENSAJE_CONFLICTO,
        )

    # Ambas lecturas encontraron una reserva ajena creada en paralelo.
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=MENSAJE_CONFLICTO,
    )


def _confirmar_huella(
    control: Session, registro: DispositivoPushRegistry, ahora: datetime,
) -> None:
    if registro.estado != "ACTIVO":
        repo_registro.marcar_activo(control, registro, ahora)
        control.commit()


def _compensar_huella(
    control: Session,
    registro: DispositivoPushRegistry,
    *,
    es_nueva: bool,
    ahora: datetime,
) -> None:
    """Compensa solo la reserva creada por esta operacion.

    No revoca una propiedad previa valida. Un fallo de compensacion deja la
    huella ``PENDIENTE`` para reconciliacion (nunca se borra evidencia).
    """
    if not es_nueva:
        return
    try:
        control.refresh(registro)
        if registro.estado == "PENDIENTE":
            repo_registro.marcar_revocado(control, registro, ahora)
            control.commit()
        else:
            control.rollback()
    except Exception:
        try:
            control.rollback()
        except Exception:  # pragma: no cover - proteccion defensiva
            pass


# --------------------------------------------------------------------------- #
# Persistencia tenant
# --------------------------------------------------------------------------- #
def _persistir_dispositivo(
    db: Session,
    datos: DispositivoPushPersistencia,
    *,
    token: str,
    ahora: datetime,
) -> DispositivoPush:
    activo = repo_dispositivos.obtener_activo_por_token(db, token)
    if activo is not None:
        if activo.usuario_id != datos.usuario_id:
            # Un token activo de otro usuario del mismo tenant es un conflicto.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=MENSAJE_CONFLICTO,
            )
        return repo_dispositivos.actualizar_metadatos(db, activo, datos, ahora)

    inactivo = repo_dispositivos.obtener_inactivo_por_usuario_y_token(
        db, datos.usuario_id, token,
    )
    if inactivo is not None:
        return repo_dispositivos.reactivar(db, inactivo, datos, ahora)

    return repo_dispositivos.registrar(db, datos)


def registrar_dispositivo(
    db: Session,
    control: Session,
    *,
    usuario_id: int,
    tenant_database_id: int,
    datos: RegistroDispositivoRequest,
) -> DispositivoPush:
    token = _token_valido(datos.token_fcm)
    ahora = _ahora()
    datos_persistencia = _datos_persistencia(
        datos, usuario_id=usuario_id, token=token,
    )

    registro, es_nueva = _reservar_huella(
        control,
        token=token,
        tenant_database_id=tenant_database_id,
        usuario_id=usuario_id,
        ahora=ahora,
    )

    try:
        dispositivo = _persistir_dispositivo(
            db, datos_persistencia, token=token, ahora=ahora,
        )
        db.commit()
        db.refresh(dispositivo)
    except HTTPException:
        db.rollback()
        _compensar_huella(
            control, registro, es_nueva=es_nueva, ahora=ahora,
        )
        raise
    except IntegrityError:
        # Carrera local: releer una vez y decidir con el estado persistido.
        db.rollback()
        try:
            dispositivo = _persistir_dispositivo(
                db, datos_persistencia, token=token, ahora=ahora,
            )
            db.commit()
            db.refresh(dispositivo)
        except HTTPException:
            db.rollback()
            _compensar_huella(
                control, registro, es_nueva=es_nueva, ahora=ahora,
            )
            raise
        except IntegrityError:
            db.rollback()
            _compensar_huella(
                control, registro, es_nueva=es_nueva, ahora=ahora,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=MENSAJE_CONFLICTO,
            ) from None
        except SQLAlchemyError:
            db.rollback()
            _compensar_huella(
                control, registro, es_nueva=es_nueva, ahora=ahora,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=MENSAJE_REGISTRO_NO_DISPONIBLE,
            ) from None
    except SQLAlchemyError:
        db.rollback()
        _compensar_huella(
            control, registro, es_nueva=es_nueva, ahora=ahora,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=MENSAJE_REGISTRO_NO_DISPONIBLE,
        ) from None

    try:
        _confirmar_huella(control, registro, ahora)
    except SQLAlchemyError:
        try:
            control.rollback()
        except Exception:  # pragma: no cover - proteccion defensiva
            pass
        # El dispositivo quedo persistido; la huella queda PENDIENTE y un
        # reintento idempotente completa la confirmacion.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=MENSAJE_REGISTRO_NO_DISPONIBLE,
        ) from None

    return dispositivo


def revocar_dispositivo(
    db: Session,
    control: Session,
    *,
    usuario_id: int,
    tenant_database_id: int,
    dispositivo_id: int,
) -> None:
    dispositivo = repo_dispositivos.obtener_por_id_y_usuario(
        db, dispositivo_id, usuario_id,
    )
    if dispositivo is None:
        # Mismo mensaje para inexistente y ajeno: no se revela su existencia.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=MENSAJE_RECURSO_NO_ENCONTRADO,
        )

    ahora = _ahora()
    if dispositivo.activo:
        try:
            repo_dispositivos.revocar(db, dispositivo, ahora)
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=MENSAJE_REVOCACION_NO_DISPONIBLE,
            ) from None

    # La propiedad global se libera solo si tenant, usuario y huella coinciden.
    try:
        huella = calcular_huella_token(dispositivo.token_fcm)
    except TokenFcmInvalido:
        # Un token ilegible no tiene propiedad global que revocar.
        return

    try:
        registro = repo_registro.obtener_por_huella(control, huella)
        if (
            registro is not None
            and registro.tenant_database_id == tenant_database_id
            and registro.usuario_id == usuario_id
            and registro.estado != "REVOCADO"
        ):
            repo_registro.marcar_revocado(control, registro, ahora)
            control.commit()
    except SQLAlchemyError:
        try:
            control.rollback()
        except Exception:  # pragma: no cover - proteccion defensiva
            pass
        # El dispositivo local ya quedo revocado y el fingerprint sigue
        # retenido (fail-closed): un reintento completa la liberacion.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=MENSAJE_REVOCACION_NO_DISPONIBLE,
        ) from None
