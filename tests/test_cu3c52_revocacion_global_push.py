# -*- coding: utf-8 -*-
"""Fase 3C.5.2 - revocacion global segura de dispositivos push invalidados.

Todo es aislado: SQLite en memoria, Control Plane simulado con un esquema
``saas_control`` adjunto y proveedor FCM falso. No se abren conexiones reales,
no se aplican migraciones, no se leen secretos y no se llama Firebase/FCM.

Limite declarado: SQLite **no** valida la concurrencia real de PostgreSQL ni
``FOR UPDATE SKIP LOCKED``. El fencing por ``lease_token`` se prueba a nivel de
consulta SQL; la concurrencia PostgreSQL queda pendiente en un entorno aislado
autorizado.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import session as database_session
from app.modules.notificaciones_push.models.models import (
    DispositivoPush,
    DispositivoPushRegistry,
    Notificacion,
    NotificacionEntrega,
    RevocacionPushGlobal,
)
from app.modules.notificaciones_push.providers import fcm
from app.modules.notificaciones_push.repositories import (
    registro_global as repo_registro,
)
from app.modules.notificaciones_push.repositories import (
    revocaciones as repo_revocaciones,
)
from app.modules.notificaciones_push.seguridad import calcular_huella_token
from app.modules.notificaciones_push.services import (
    revocaciones as servicio_revocaciones,
)
from app.modules.notificaciones_push.services.worker_fcm import (
    ConfiguracionWorker,
    procesar_entregas,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "database" / "tenant_migrations"

AHORA = datetime(2026, 11, 23, 12, 0, 0)
LEASE = timedelta(seconds=120)

TENANT_A = 11
TENANT_B = 22
USUARIO = 9001

TOKEN_A = "token-fcm-android-aaaaaaaaaaaa"
TOKEN_B = "token-fcm-ios-bbbbbbbbbbbbbbbbbb"

# --------------------------------------------------------------------------- #
# Esquema tenant aislado
# --------------------------------------------------------------------------- #
DDL_TENANT = (
    "CREATE TABLE usuario (id INTEGER PRIMARY KEY, estado BOOLEAN NOT NULL)",
    """CREATE TABLE dispositivo_push (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuario(id),
        token_fcm VARCHAR(1024) NOT NULL,
        plataforma VARCHAR(10) NOT NULL,
        instalacion_id VARCHAR(100),
        app_version VARCHAR(50),
        nombre_dispositivo VARCHAR(120),
        activo BOOLEAN NOT NULL DEFAULT 1,
        fecha_registro TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fecha_ultimo_uso TIMESTAMP,
        revocado_en TIMESTAMP,
        CONSTRAINT ck_dispositivo_push_revocacion CHECK (
            (activo AND revocado_en IS NULL) OR (NOT activo)
        )
    )""",
    """CREATE TABLE notificacion (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuario(id),
        tipo VARCHAR(30) NOT NULL,
        evento_clave VARCHAR(150) NOT NULL,
        titulo VARCHAR(150) NOT NULL,
        cuerpo TEXT NOT NULL,
        datos JSON,
        cita_id INTEGER,
        leida BOOLEAN NOT NULL DEFAULT 0,
        fecha_lectura TIMESTAMP,
        cancelada_en TIMESTAMP,
        fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_notificacion_evento_clave UNIQUE (evento_clave)
    )""",
    """CREATE TABLE notificacion_entrega (
        id INTEGER PRIMARY KEY,
        notificacion_id INTEGER NOT NULL REFERENCES notificacion(id),
        dispositivo_id INTEGER NOT NULL REFERENCES dispositivo_push(id),
        estado VARCHAR(25) NOT NULL DEFAULT 'PENDIENTE',
        proveedor VARCHAR(20) NOT NULL DEFAULT 'FCM',
        proveedor_mensaje_id VARCHAR(255),
        intentos SMALLINT NOT NULL DEFAULT 0,
        proximo_intento TIMESTAMP,
        bloqueado_hasta TIMESTAMP,
        lease_token VARCHAR(64),
        codigo_ultimo_error VARCHAR(100),
        fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fecha_aceptado TIMESTAMP,
        CONSTRAINT uq_notificacion_entrega_notificacion_dispositivo
            UNIQUE (notificacion_id, dispositivo_id),
        CONSTRAINT ck_notificacion_entrega_estado CHECK (
            estado IN ('PENDIENTE','PROCESANDO','ACEPTADO_POR_FCM','FALLIDO',
                       'INVALIDO','OMITIDO')
        )
    )""",
    """CREATE TABLE preferencia_notificacion (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuario(id),
        push_habilitado BOOLEAN NOT NULL DEFAULT 1,
        citas_creadas BOOLEAN NOT NULL DEFAULT 1,
        citas_confirmadas BOOLEAN NOT NULL DEFAULT 1,
        citas_reprogramadas BOOLEAN NOT NULL DEFAULT 1,
        citas_canceladas BOOLEAN NOT NULL DEFAULT 1,
        recordatorios BOOLEAN NOT NULL DEFAULT 1,
        fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_preferencia_notificacion_usuario UNIQUE (usuario_id)
    )""",
    """CREATE TABLE dispositivo_push_revocacion (
        id INTEGER PRIMARY KEY,
        dispositivo_id INTEGER NOT NULL
            REFERENCES dispositivo_push(id) ON DELETE RESTRICT,
        usuario_id INTEGER NOT NULL REFERENCES usuario(id) ON DELETE RESTRICT,
        token_fingerprint VARCHAR(64) NOT NULL,
        estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
        intentos SMALLINT NOT NULL DEFAULT 0,
        proximo_intento TIMESTAMP,
        bloqueado_hasta TIMESTAMP,
        lease_token VARCHAR(64),
        codigo_ultimo_error VARCHAR(100),
        fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fecha_procesado TIMESTAMP,
        CONSTRAINT uq_dispositivo_push_revocacion_dispositivo_huella
            UNIQUE (dispositivo_id, token_fingerprint),
        CONSTRAINT ck_dispositivo_push_revocacion_huella CHECK (
            length(token_fingerprint) = 64
            AND token_fingerprint = lower(token_fingerprint)
        ),
        CONSTRAINT ck_dispositivo_push_revocacion_estado CHECK (
            estado IN ('PENDIENTE','PROCESANDO','COMPLETADA','NO_ENCONTRADO',
                       'RECHAZADA','FALLIDA')
        ),
        CONSTRAINT ck_dispositivo_push_revocacion_intentos CHECK (intentos >= 0)
    )""",
)

# --------------------------------------------------------------------------- #
# Esquema Control Plane aislado (adjunto como ``saas_control``)
# --------------------------------------------------------------------------- #
DDL_CONTROL = """
CREATE TABLE saas_control.dispositivo_push_registry (
    id INTEGER PRIMARY KEY,
    token_fingerprint VARCHAR(64) NOT NULL,
    tenant_database_id INTEGER NOT NULL,
    usuario_id BIGINT NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'ACTIVO',
    huella_version SMALLINT NOT NULL DEFAULT 1,
    fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_revocacion TIMESTAMP,
    CONSTRAINT uq_dispositivo_push_registry_huella UNIQUE (token_fingerprint),
    CONSTRAINT ck_dispositivo_push_registry_huella CHECK (
        length(token_fingerprint) = 64
        AND token_fingerprint = lower(token_fingerprint)
    ),
    CONSTRAINT ck_dispositivo_push_registry_estado CHECK (
        estado IN ('PENDIENTE', 'ACTIVO', 'REVOCADO')
    ),
    CONSTRAINT ck_dispositivo_push_registry_revocacion CHECK (
        (estado = 'REVOCADO' AND fecha_revocacion IS NOT NULL)
        OR (estado <> 'REVOCADO' AND fecha_revocacion IS NULL)
    )
)
"""


def _engine_tenant() -> object:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        for ddl in DDL_TENANT:
            cursor.execute(ddl)
        cursor.execute("INSERT INTO usuario (id, estado) VALUES (9001, 1)")
        cursor.execute("INSERT INTO usuario (id, estado) VALUES (9002, 1)")
        raw.commit()
    finally:
        raw.close()
    return engine


def _engine_control() -> object:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("ATTACH DATABASE ':memory:' AS saas_control")
        cursor.execute(
            "CREATE TABLE saas_control.tenant_database (id INTEGER PRIMARY KEY)"
        )
        cursor.execute(
            "INSERT INTO saas_control.tenant_database (id) VALUES (11),(22)"
        )
        cursor.execute(DDL_CONTROL)
        raw.commit()
    finally:
        raw.close()
    return engine


@pytest.fixture
def db():
    engine = _engine_tenant()
    with Session(engine, autoflush=False) as sesion:
        yield sesion
    engine.dispose()


@pytest.fixture
def control():
    engine = _engine_control()
    with Session(engine, autoflush=False) as sesion:
        yield sesion
    engine.dispose()


# --------------------------------------------------------------------------- #
# Datos
# --------------------------------------------------------------------------- #
def _dispositivo(
    db: Session, *, usuario_id: int = USUARIO, token: str = TOKEN_A,
    activo: bool = True, id_: int | None = None,
) -> DispositivoPush:
    dispositivo = DispositivoPush(
        usuario_id=usuario_id,
        token_fcm=token,
        plataforma="ANDROID",
        activo=activo,
        revocado_en=None if activo else AHORA,
    )
    if id_ is not None:
        dispositivo.id = id_
    db.add(dispositivo)
    db.flush()
    return dispositivo


def _notificacion(
    db: Session, *, usuario_id: int = USUARIO, evento: str = "evt-1",
) -> Notificacion:
    notificacion = Notificacion(
        usuario_id=usuario_id,
        tipo="CITA_CREADA",
        evento_clave=evento,
        titulo="Cita registrada",
        cuerpo="Tu cita fue registrada.",
        datos={"ruta": "/citas"},
    )
    db.add(notificacion)
    db.flush()
    return notificacion


def _entrega(
    db: Session, *, notificacion_id: int, dispositivo_id: int,
    estado: str = "PENDIENTE",
) -> NotificacionEntrega:
    entrega = NotificacionEntrega(
        notificacion_id=notificacion_id,
        dispositivo_id=dispositivo_id,
        estado=estado,
    )
    db.add(entrega)
    db.flush()
    return entrega


def _registro(
    control: Session,
    *,
    token: str,
    tenant_id: int = TENANT_A,
    usuario_id: int = USUARIO,
    estado: str = "ACTIVO",
) -> DispositivoPushRegistry:
    registro = DispositivoPushRegistry(
        token_fingerprint=calcular_huella_token(token),
        tenant_database_id=tenant_id,
        usuario_id=usuario_id,
        estado=estado,
        huella_version=1,
        fecha_revocacion=AHORA if estado == "REVOCADO" else None,
    )
    control.add(registro)
    control.flush()
    return registro


def _solicitudes(db: Session) -> list[RevocacionPushGlobal]:
    db.expire_all()
    return list(db.scalars(
        select(RevocacionPushGlobal).order_by(RevocacionPushGlobal.id)
    ))


# --------------------------------------------------------------------------- #
# Proveedores falsos
# --------------------------------------------------------------------------- #
class ProveedorInvalido:
    configurado = True

    def __init__(self, codigo: str = "UNREGISTERED") -> None:
        self.codigo = codigo
        self.llamadas = 0

    def enviar(self, **_kwargs):
        self.llamadas += 1
        return fcm.ResultadoFcm(fcm.TOKEN_NO_REGISTRADO, self.codigo)


class ProveedorClasificacion:
    configurado = True

    def __init__(self, clasificacion: str, codigo: str) -> None:
        self.clasificacion = clasificacion
        self.codigo = codigo

    def enviar(self, **_kwargs):
        return fcm.ResultadoFcm(self.clasificacion, self.codigo)


class ProveedorNoConfigurado:
    configurado = False

    def enviar(self, **_kwargs):  # pragma: no cover - no debe invocarse
        raise AssertionError("un proveedor no configurado no debe enviar")


def _ejecutar_worker(db: Session, proveedor, *, ahora=AHORA):
    return procesar_entregas(
        db,
        proveedor=proveedor,
        configuracion=ConfiguracionWorker(tamano_lote=20),
        reloj=lambda: ahora,
    )


# --------------------------------------------------------------------------- #
# A. Clasificacion FCM -> solicitud de revocacion
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "codigo", ["UNREGISTERED", "INVALID_REGISTRATION", "SENDER_ID_MISMATCH"],
)
def test_codigos_invalidantes_crean_solicitud_durable(db, codigo):
    dispositivo = _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id)
    db.commit()

    resultado = _ejecutar_worker(db, ProveedorInvalido(codigo))

    assert resultado.invalidadas == 1
    assert resultado.revocaciones_solicitadas == 1
    solicitudes = _solicitudes(db)
    assert len(solicitudes) == 1
    assert solicitudes[0].estado == "PENDIENTE"
    assert solicitudes[0].token_fingerprint == calcular_huella_token(TOKEN_A)
    db.refresh(dispositivo)
    assert dispositivo.activo is False


@pytest.mark.parametrize(
    "proveedor",
    [
        ProveedorClasificacion(fcm.PERMANENTE, "PAYLOAD_INVALIDO"),
        ProveedorClasificacion(fcm.ERROR_CONFIGURACION, "PROVEEDOR_NO_AUTORIZADO"),
        ProveedorClasificacion(fcm.REINTENTABLE, "PROVEEDOR_RATE_LIMIT"),
        ProveedorClasificacion(fcm.REINTENTABLE, "PROVEEDOR_NO_DISPONIBLE"),
        ProveedorClasificacion(fcm.REINTENTABLE, "PROVEEDOR_TIMEOUT"),
        ProveedorClasificacion(fcm.REINTENTABLE, "PROVEEDOR_RED"),
        ProveedorNoConfigurado(),
    ],
    ids=[
        "400-generico", "401-403", "429", "5xx", "timeout", "red",
        "no-configurado",
    ],
)
def test_errores_no_invalidantes_no_crean_solicitud(db, proveedor):
    dispositivo = _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id)
    db.commit()

    _ejecutar_worker(db, proveedor)

    assert _solicitudes(db) == []
    db.refresh(dispositivo)
    assert dispositivo.activo is True


def test_worker_no_abre_control_plane(db, monkeypatch):
    def _prohibido():  # pragma: no cover - debe no invocarse
        raise AssertionError("el worker FCM no debe abrir el Control Plane")

    monkeypatch.setattr(database_session, "open_control_session", _prohibido)
    dispositivo = _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id)
    db.commit()

    _ejecutar_worker(db, ProveedorInvalido())

    assert len(_solicitudes(db)) == 1


# --------------------------------------------------------------------------- #
# B. Outbox tenant, idempotencia y transacciones
# --------------------------------------------------------------------------- #
def test_solicitud_se_crea_una_sola_vez(db):
    dispositivo = _dispositivo(db)
    primera = _notificacion(db, evento="evt-1")
    segunda = _notificacion(db, evento="evt-2")
    _entrega(db, notificacion_id=primera.id, dispositivo_id=dispositivo.id)
    _entrega(db, notificacion_id=segunda.id, dispositivo_id=dispositivo.id)
    db.commit()

    _ejecutar_worker(db, ProveedorInvalido())

    assert len(_solicitudes(db)) == 1


def test_clave_de_idempotencia_impide_duplicados(db):
    dispositivo = _dispositivo(db)
    db.commit()
    huella = calcular_huella_token(TOKEN_A)

    primera, creada_1 = servicio_revocaciones.registrar_solicitud_revocacion(
        db, dispositivo, AHORA,
    )
    segunda, creada_2 = servicio_revocaciones.registrar_solicitud_revocacion(
        db, dispositivo, AHORA + timedelta(seconds=5),
    )
    db.commit()

    assert creada_1 is True
    assert creada_2 is False
    assert primera.id == segunda.id
    assert db.scalar(
        select(func.count()).select_from(RevocacionPushGlobal)
    ) == 1
    assert primera.token_fingerprint == huella


def test_no_se_persiste_el_token_original(db):
    dispositivo = _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id)
    db.commit()
    _ejecutar_worker(db, ProveedorInvalido())

    columnas = set(RevocacionPushGlobal.__table__.columns.keys())
    assert "token_fcm" not in columnas
    assert "token" not in columnas
    solicitud = _solicitudes(db)[0]
    assert TOKEN_A not in repr(solicitud)
    assert solicitud.token_fingerprint != TOKEN_A


def test_rollback_conjunto_de_desactivacion_y_solicitud(db, monkeypatch):
    dispositivo = _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id)
    db.commit()

    def _falla(*_args, **_kwargs):
        raise RuntimeError("fallo simulado al persistir la solicitud")

    monkeypatch.setattr(
        "app.modules.notificaciones_push.services.worker_fcm."
        "registrar_solicitud_revocacion",
        _falla,
    )
    with pytest.raises(RuntimeError):
        _ejecutar_worker(db, ProveedorInvalido())

    db.rollback()
    db.expire_all()
    assert _solicitudes(db) == []
    dispositivo = db.get(DispositivoPush, dispositivo.id)
    assert dispositivo.activo is True
    entrega = db.scalar(select(NotificacionEntrega))
    # El claim se confirmo antes de la red (3C.5); lo que se revierte es el
    # resultado INVALIDO junto con la desactivacion y la solicitud.
    assert entrega.estado == "PROCESANDO"
    assert entrega.lease_token is not None


# --------------------------------------------------------------------------- #
# C. Control Plane y aislamiento tenant
# --------------------------------------------------------------------------- #
def test_revoca_la_pareja_tenant_huella_correcta(db, control):
    dispositivo = _dispositivo(db)
    db.commit()
    servicio_revocaciones.registrar_solicitud_revocacion(db, dispositivo, AHORA)
    db.commit()
    registro = _registro(control, token=TOKEN_A)
    control.commit()

    resultado = servicio_revocaciones.procesar_revocaciones(
        db, control, tenant_database_id=TENANT_A, reloj=lambda: AHORA,
    )

    assert resultado.completadas == 1
    control.refresh(registro)
    assert registro.estado == "REVOCADO"
    assert registro.fecha_revocacion is not None
    solicitud = _solicitudes(db)[0]
    assert solicitud.estado == "COMPLETADA"
    assert solicitud.codigo_ultimo_error == "REVOCACION_GLOBAL_CONFIRMADA"


def test_repeticion_es_idempotente_y_conserva_fecha(db, control):
    dispositivo = _dispositivo(db)
    db.commit()
    servicio_revocaciones.registrar_solicitud_revocacion(db, dispositivo, AHORA)
    db.commit()
    registro = _registro(control, token=TOKEN_A)
    control.commit()

    servicio_revocaciones.procesar_revocaciones(
        db, control, tenant_database_id=TENANT_A, reloj=lambda: AHORA,
    )
    control.refresh(registro)
    fecha_original = registro.fecha_revocacion

    # Nueva solicitud equivalente (p. ej. tras caida antes del ACK).
    solicitud = _solicitudes(db)[0]
    solicitud.estado = "PENDIENTE"
    db.commit()
    siguiente = AHORA + timedelta(minutes=10)
    resultado = servicio_revocaciones.procesar_revocaciones(
        db, control, tenant_database_id=TENANT_A, reloj=lambda: siguiente,
    )

    assert resultado.ya_revocadas == 1
    control.refresh(registro)
    assert registro.fecha_revocacion == fecha_original
    assert _solicitudes(db)[0].estado == "COMPLETADA"


def test_tenant_b_no_puede_revocar_huella_del_tenant_a(db, control):
    dispositivo = _dispositivo(db)
    db.commit()
    servicio_revocaciones.registrar_solicitud_revocacion(db, dispositivo, AHORA)
    db.commit()
    # Misma huella y mismo usuario_id local, pero propiedad del tenant A.
    registro = _registro(control, token=TOKEN_A, tenant_id=TENANT_A)
    control.commit()

    resultado = servicio_revocaciones.procesar_revocaciones(
        db, control, tenant_database_id=TENANT_B, reloj=lambda: AHORA,
    )

    assert resultado.rechazadas == 1
    control.refresh(registro)
    assert registro.estado == "ACTIVO"
    assert registro.fecha_revocacion is None
    solicitud = _solicitudes(db)[0]
    assert solicitud.estado == "RECHAZADA"
    assert solicitud.codigo_ultimo_error == "HUELLA_OTRO_TENANT"


def test_dos_tenants_con_usuario_local_igual_permanecen_aislados(db, control):
    dispositivo = _dispositivo(db, usuario_id=USUARIO)
    db.commit()
    servicio_revocaciones.registrar_solicitud_revocacion(db, dispositivo, AHORA)
    db.commit()
    registro_a = _registro(
        control, token=TOKEN_A, tenant_id=TENANT_A, usuario_id=USUARIO,
    )
    registro_b = _registro(
        control, token=TOKEN_B, tenant_id=TENANT_B, usuario_id=USUARIO,
    )
    control.commit()

    servicio_revocaciones.procesar_revocaciones(
        db, control, tenant_database_id=TENANT_A, reloj=lambda: AHORA,
    )

    control.refresh(registro_a)
    control.refresh(registro_b)
    assert registro_a.estado == "REVOCADO"
    assert registro_b.estado == "ACTIVO"
    assert registro_b.fecha_revocacion is None


def test_huella_inexistente_no_inserta_ni_busca_en_otros_tenants(db, control):
    dispositivo = _dispositivo(db)
    db.commit()
    servicio_revocaciones.registrar_solicitud_revocacion(db, dispositivo, AHORA)
    db.commit()

    resultado = servicio_revocaciones.procesar_revocaciones(
        db, control, tenant_database_id=TENANT_A, reloj=lambda: AHORA,
    )

    assert resultado.no_encontradas == 1
    assert control.scalar(
        select(func.count()).select_from(DispositivoPushRegistry)
    ) == 0
    solicitud = _solicitudes(db)[0]
    assert solicitud.estado == "NO_ENCONTRADO"
    assert solicitud.codigo_ultimo_error == "HUELLA_NO_ENCONTRADA"


def test_estado_global_inesperado_falla_cerrado(db, control, monkeypatch):
    dispositivo = _dispositivo(db)
    db.commit()
    servicio_revocaciones.registrar_solicitud_revocacion(db, dispositivo, AHORA)
    db.commit()
    registro = _registro(control, token=TOKEN_A)
    control.commit()

    monkeypatch.setattr(
        repo_registro,
        "revocar_huella_acotada",
        lambda *_a, **_k: repo_registro.ResultadoRevocacionGlobal(
            repo_registro.REVOCACION_ESTADO_INESPERADO, "DESCONOCIDO",
        ),
    )
    resultado = servicio_revocaciones.procesar_revocaciones(
        db, control, tenant_database_id=TENANT_A, reloj=lambda: AHORA,
    )

    assert resultado.reprogramadas == 1
    control.refresh(registro)
    assert registro.estado == "ACTIVO"
    solicitud = _solicitudes(db)[0]
    assert solicitud.estado == "PENDIENTE"
    assert solicitud.codigo_ultimo_error == "ESTADO_GLOBAL_INESPERADO"
    assert solicitud.proximo_intento is not None


def test_solo_se_actualiza_la_fila_objetivo(db, control):
    dispositivo = _dispositivo(db)
    db.commit()
    servicio_revocaciones.registrar_solicitud_revocacion(db, dispositivo, AHORA)
    db.commit()
    objetivo = _registro(control, token=TOKEN_A, tenant_id=TENANT_A)
    otra = _registro(control, token=TOKEN_B, tenant_id=TENANT_A)
    control.commit()

    servicio_revocaciones.procesar_revocaciones(
        db, control, tenant_database_id=TENANT_A, reloj=lambda: AHORA,
    )

    control.refresh(objetivo)
    control.refresh(otra)
    assert objetivo.estado == "REVOCADO"
    assert otra.estado == "ACTIVO"


# --------------------------------------------------------------------------- #
# D. Fallos entre bases y reintentos
# --------------------------------------------------------------------------- #
def test_caida_antes_del_commit_control_plane_no_pierde_la_solicitud(
    db, control, monkeypatch,
):
    dispositivo = _dispositivo(db)
    db.commit()
    servicio_revocaciones.registrar_solicitud_revocacion(db, dispositivo, AHORA)
    db.commit()
    registro = _registro(control, token=TOKEN_A)
    control.commit()

    def _caida(*_a, **_k):
        raise SQLAlchemyError("fallo simulado del Control Plane")

    monkeypatch.setattr(repo_registro, "revocar_huella_acotada", _caida)
    resultado = servicio_revocaciones.procesar_revocaciones(
        db, control, tenant_database_id=TENANT_A, reloj=lambda: AHORA,
    )

    assert resultado.reprogramadas == 1
    control.refresh(registro)
    assert registro.estado == "ACTIVO"
    solicitud = _solicitudes(db)[0]
    assert solicitud.estado == "PENDIENTE"
    assert solicitud.codigo_ultimo_error == "CONTROL_PLANE_NO_DISPONIBLE"


def test_caida_despues_del_commit_control_plane_y_antes_del_ack(
    db, control, monkeypatch,
):
    dispositivo = _dispositivo(db)
    db.commit()
    servicio_revocaciones.registrar_solicitud_revocacion(db, dispositivo, AHORA)
    db.commit()
    registro = _registro(control, token=TOKEN_A)
    control.commit()

    original = repo_revocaciones.actualizar_resultado
    llamadas = {"n": 0}

    def _falla_una_vez(*args, **kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise RuntimeError("caida simulada durante el ACK tenant")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        repo_revocaciones, "actualizar_resultado", _falla_una_vez,
    )
    with pytest.raises(RuntimeError):
        servicio_revocaciones.procesar_revocaciones(
            db, control, tenant_database_id=TENANT_A, reloj=lambda: AHORA,
        )

    # El Control Plane ya quedo confirmado.
    control.refresh(registro)
    assert registro.estado == "REVOCADO"
    assert _solicitudes(db)[0].estado == "PROCESANDO"

    # El lease vence y el reintento detecta REVOCADO y finaliza.
    monkeypatch.setattr(
        repo_revocaciones, "actualizar_resultado", original,
    )
    resultado = servicio_revocaciones.procesar_revocaciones(
        db,
        control,
        tenant_database_id=TENANT_A,
        reloj=lambda: AHORA + LEASE + timedelta(seconds=1),
    )
    assert resultado.ya_revocadas == 1
    assert _solicitudes(db)[0].estado == "COMPLETADA"


def test_lease_vigente_no_se_reclama_y_vencido_si(db):
    dispositivo = _dispositivo(db)
    db.commit()
    solicitud, _ = repo_revocaciones.crear_si_no_existe(
        db,
        dispositivo_id=dispositivo.id,
        usuario_id=USUARIO,
        token_fingerprint=calcular_huella_token(TOKEN_A),
        cuando=AHORA,
    )
    db.commit()

    with Session(db.get_bind(), autoflush=False) as sesion_a:
        lote_a = repo_revocaciones.reclamar_lote(
            sesion_a, ahora=AHORA, lease_token="lease-a",
            lease_hasta=AHORA + LEASE, limite=10,
        )
        assert [item.id for item in lote_a] == [solicitud.id]
        sesion_a.commit()

    with Session(db.get_bind(), autoflush=False) as sesion_b:
        # Vigente: no se reclama.
        assert repo_revocaciones.reclamar_lote(
            sesion_b, ahora=AHORA + timedelta(seconds=30),
            lease_token="lease-b", lease_hasta=AHORA + LEASE, limite=10,
        ) == []
        sesion_b.rollback()

    with Session(db.get_bind(), autoflush=False) as sesion_c:
        lote_c = repo_revocaciones.reclamar_lote(
            sesion_c, ahora=AHORA + LEASE + timedelta(seconds=1),
            lease_token="lease-nuevo",
            lease_hasta=AHORA + 2 * LEASE, limite=10,
        )
        assert [item.id for item in lote_c] == [solicitud.id]
        sesion_c.commit()

    db.expire_all()
    fila = db.get(RevocacionPushGlobal, solicitud.id)
    assert fila.lease_token == "lease-nuevo"


def test_fencing_worker_antiguo_no_sobrescribe_lease_nuevo(db):
    dispositivo = _dispositivo(db)
    db.commit()
    solicitud, _ = repo_revocaciones.crear_si_no_existe(
        db,
        dispositivo_id=dispositivo.id,
        usuario_id=USUARIO,
        token_fingerprint=calcular_huella_token(TOKEN_A),
        cuando=AHORA,
    )
    db.commit()

    with Session(db.get_bind(), autoflush=False) as sesion_a:
        repo_revocaciones.reclamar_lote(
            sesion_a, ahora=AHORA, lease_token="lease-a",
            lease_hasta=AHORA + LEASE, limite=1,
        )
        sesion_a.commit()

    with Session(db.get_bind(), autoflush=False) as sesion_b:
        repo_revocaciones.reclamar_lote(
            sesion_b, ahora=AHORA + LEASE + timedelta(seconds=1),
            lease_token="lease-b", lease_hasta=AHORA + 2 * LEASE, limite=1,
        )
        sesion_b.commit()

    with Session(db.get_bind(), autoflush=False) as sesion_a:
        aplicado = repo_revocaciones.actualizar_resultado(
            sesion_a, solicitud.id, lease_token="lease-a",
            valores={"estado": "COMPLETADA", "lease_token": None},
        )
        sesion_a.commit()
    assert aplicado is False

    db.expire_all()
    fila = db.get(RevocacionPushGlobal, solicitud.id)
    assert fila.estado == "PROCESANDO"
    assert fila.lease_token == "lease-b"


def test_procesador_exige_tenant_database_id_interno(db, control):
    for invalido in (None, 0, -1, True, "11"):
        with pytest.raises(ValueError):
            servicio_revocaciones.procesar_revocaciones(
                db, control, tenant_database_id=invalido,  # type: ignore[arg-type]
            )


def test_procesador_rechaza_tenant_distinto_al_contexto_resuelto(db, control):
    from types import SimpleNamespace

    db.info["tenant_context"] = SimpleNamespace(tenant_database_id=TENANT_B)
    with pytest.raises(ValueError):
        servicio_revocaciones.procesar_revocaciones(
            db, control, tenant_database_id=TENANT_A,
        )


def test_sin_control_plane_no_reclama_ni_finge_exito(db):
    dispositivo = _dispositivo(db)
    db.commit()
    servicio_revocaciones.registrar_solicitud_revocacion(db, dispositivo, AHORA)
    db.commit()

    resultado = servicio_revocaciones.procesar_revocaciones(
        db, None, tenant_database_id=TENANT_A, reloj=lambda: AHORA,  # type: ignore[arg-type]
    )

    assert resultado.error_control is True
    assert resultado.reclamadas == 0
    assert _solicitudes(db)[0].estado == "PENDIENTE"


# --------------------------------------------------------------------------- #
# E. Privacidad
# --------------------------------------------------------------------------- #
def test_ni_token_ni_huella_aparecen_en_logs(db, control, caplog):
    caplog.set_level(logging.DEBUG)
    dispositivo = _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id)
    db.commit()
    _registro(control, token=TOKEN_A)
    control.commit()

    _ejecutar_worker(db, ProveedorInvalido())
    servicio_revocaciones.procesar_revocaciones(
        db, control, tenant_database_id=TENANT_A, reloj=lambda: AHORA,
    )

    huella = calcular_huella_token(TOKEN_A)
    assert TOKEN_A not in caplog.text
    assert huella not in caplog.text
    assert huella not in repr(_solicitudes(db)[0])


# --------------------------------------------------------------------------- #
# F. Artefactos de migracion y manifiesto
# --------------------------------------------------------------------------- #
def test_migracion_v9_es_aditiva_y_sin_transaccion_propia():
    sql = (
        MIGRACIONES
        / "versions" / "20261123000000_cu3c52_revocacion_global_push.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE public.dispositivo_push_revocacion" in sql
    assert "uq_dispositivo_push_revocacion_dispositivo_huella" in sql
    assert "token_fingerprint" in sql
    assert "token_fcm VARCHAR" not in sql
    assert "DELETE FROM" not in sql
    assert not re.search(r"(?im)^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", sql)


def test_manifiesto_declara_v9_y_no_aplicado():
    from app.modules.administracion_saas.tenant_migrator import (
        EXPECTED_VERSIONS,
        load_bundle,
    )

    bundle = load_bundle(MIGRACIONES)
    assert bundle.schema_version == "v9-sprint5-revocacion-global-push"
    ultima = bundle.migrations[-1]
    assert ultima.version == "20261123000000"
    assert ultima.name == "cu3c52_revocacion_global_push"
    assert EXPECTED_VERSIONS[-1] == "20261123000000"
    assert "v8-sprint5-worker-fcm-push" in bundle.allowed_source_schema_versions


def test_preflight_y_verify_siguen_siendo_solo_lectura():
    for nombre in ("000_preflight_cu3c5.sql", "999_verify_cu3c5.sql"):
        sql = (MIGRACIONES / nombre).read_text(encoding="utf-8")
        assert "BEGIN TRANSACTION READ ONLY;" in sql
        assert sql.rstrip().endswith("ROLLBACK;")
        assert "CREATE TABLE" not in sql
        assert "ALTER TABLE" not in sql
