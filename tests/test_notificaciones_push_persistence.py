"""Fase 3C.2 - pruebas aisladas de persistencia de notificaciones push.

Ninguna prueba abre conexiones reales: se usa SQLite en memoria, incluido un
``saas_control`` adjunto para el registro global de huellas. No se llama a FCM,
no se leen secretos y no se aplican migraciones.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.base import Base  # noqa: F401  (registra metadatos compartidos)
from app.modules.notificaciones_push.models.models import (
    CitaEvento,
    DispositivoPush,
    DispositivoPushRegistry,
    Notificacion,
    NotificacionEntrega,
    PreferenciaNotificacion,
    RevocacionPushGlobal,
)
from app.modules.notificaciones_push.repositories import (
    dispositivos as repo_dispositivos,
    entregas as repo_entregas,
    eventos_cita as repo_eventos,
    notificaciones as repo_notificaciones,
    preferencias as repo_preferencias,
    registro_global,
)
from app.modules.notificaciones_push.schemas.schemas import (
    CitaEventoPersistencia,
    CitaEventoVista,
    DispositivoPushPersistencia,
    DispositivoPushVista,
    NotificacionEntregaPersistencia,
    NotificacionPersistencia,
    NotificacionVista,
    PreferenciaNotificacionActualizacion,
)
from app.modules.notificaciones_push.seguridad import calcular_huella_token

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = ROOT / "database" / "tenant_migrations"

TABLAS_TENANT = (
    DispositivoPush.__table__,
    Notificacion.__table__,
    NotificacionEntrega.__table__,
    PreferenciaNotificacion.__table__,
    CitaEvento.__table__,
    RevocacionPushGlobal.__table__,
)

# DDL SQLite en memoria que replica la migracion tenant. Se escribe a mano
# (igual que el resto de suites del repositorio) porque SQLite necesita
# ``INTEGER PRIMARY KEY`` para autoincrementar; en PostgreSQL la identidad la
# genera ``GENERATED ALWAYS AS IDENTITY``. La prueba estatica final verifica
# que los nombres de constraints/indices del modelo coincidan con la migracion.
DDL_TENANT = (
    """CREATE TABLE usuario (
        id INTEGER PRIMARY KEY, correo TEXT
    )""",
    """CREATE TABLE cita (id INTEGER PRIMARY KEY)""",
    """CREATE TABLE dispositivo_push (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuario(id) ON DELETE RESTRICT,
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
        CONSTRAINT ck_dispositivo_push_plataforma CHECK (
            plataforma IN ('ANDROID','IOS','WEB')
        ),
        CONSTRAINT ck_dispositivo_push_revocacion CHECK (
            (activo AND revocado_en IS NULL) OR (NOT activo)
        )
    )""",
    """CREATE UNIQUE INDEX uq_dispositivo_push_token_activo
        ON dispositivo_push (token_fcm) WHERE activo = 1""",
    """CREATE INDEX ix_dispositivo_push_usuario_activo
        ON dispositivo_push (usuario_id, activo)""",
    """CREATE TABLE notificacion (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuario(id) ON DELETE RESTRICT,
        tipo VARCHAR(30) NOT NULL,
        evento_clave VARCHAR(150) NOT NULL,
        titulo VARCHAR(150) NOT NULL,
        cuerpo TEXT NOT NULL,
        datos JSON,
        cita_id INTEGER REFERENCES cita(id) ON DELETE SET NULL,
        leida BOOLEAN NOT NULL DEFAULT 0,
        fecha_lectura TIMESTAMP,
        cancelada_en TIMESTAMP,
        fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_notificacion_evento_clave UNIQUE (evento_clave),
        CONSTRAINT ck_notificacion_tipo CHECK (
            tipo IN ('CITA_CREADA','CITA_CONFIRMADA','CITA_REPROGRAMADA',
                     'CITA_CANCELADA','RECORDATORIO_CITA',
                     'ACTUALIZACION_OPERATIVA')
        ),
        CONSTRAINT ck_notificacion_lectura_coherente CHECK (
            (leida AND fecha_lectura IS NOT NULL)
            OR (NOT leida AND fecha_lectura IS NULL)
        )
    )""",
    """CREATE INDEX ix_notificacion_usuario_fecha
        ON notificacion (usuario_id, fecha_creacion DESC)""",
    """CREATE INDEX ix_notificacion_usuario_leida
        ON notificacion (usuario_id, leida)""",
    """CREATE TABLE notificacion_entrega (
        id INTEGER PRIMARY KEY,
        notificacion_id INTEGER NOT NULL
            REFERENCES notificacion(id) ON DELETE RESTRICT,
        dispositivo_id INTEGER NOT NULL
            REFERENCES dispositivo_push(id) ON DELETE RESTRICT,
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
        ),
        CONSTRAINT ck_notificacion_entrega_proveedor CHECK (proveedor = 'FCM'),
        CONSTRAINT ck_notificacion_entrega_intentos CHECK (intentos >= 0)
    )""",
    """CREATE UNIQUE INDEX uq_notificacion_entrega_proveedor_mensaje
        ON notificacion_entrega (proveedor_mensaje_id)
        WHERE proveedor_mensaje_id IS NOT NULL""",
    """CREATE INDEX ix_notificacion_entrega_cola
        ON notificacion_entrega (estado, proximo_intento)""",
    """CREATE INDEX ix_notificacion_entrega_lease
        ON notificacion_entrega (bloqueado_hasta)
        WHERE estado = 'PROCESANDO'""",
    """CREATE TABLE preferencia_notificacion (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuario(id) ON DELETE RESTRICT,
        push_habilitado BOOLEAN NOT NULL DEFAULT 1,
        citas_creadas BOOLEAN NOT NULL DEFAULT 1,
        citas_confirmadas BOOLEAN NOT NULL DEFAULT 1,
        citas_reprogramadas BOOLEAN NOT NULL DEFAULT 1,
        citas_canceladas BOOLEAN NOT NULL DEFAULT 1,
        recordatorios BOOLEAN NOT NULL DEFAULT 1,
        fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_preferencia_notificacion_usuario UNIQUE (usuario_id)
    )""",
    """CREATE TABLE cita_evento (
        id INTEGER PRIMARY KEY,
        cita_id INTEGER NOT NULL REFERENCES cita(id) ON DELETE RESTRICT,
        tipo VARCHAR(30) NOT NULL,
        estado_anterior VARCHAR(20),
        estado_nuevo VARCHAR(20),
        fecha_anterior DATE,
        hora_anterior TIME,
        fecha_nueva DATE,
        hora_nueva TIME,
        fecha_evento TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actor_usuario_id INTEGER REFERENCES usuario(id) ON DELETE SET NULL,
        clave_idempotencia VARCHAR(150) NOT NULL,
        CONSTRAINT uq_cita_evento_clave_idempotencia UNIQUE (clave_idempotencia),
        CONSTRAINT ck_cita_evento_tipo CHECK (
            tipo IN ('CITA_CREADA','CITA_ESTADO_CAMBIADO','CITA_REPROGRAMADA')
        ),
        CONSTRAINT ck_cita_evento_estado_anterior CHECK (
            estado_anterior IS NULL
            OR estado_anterior IN ('PROGRAMADA','CONFIRMADA','EN_ESPERA',
                                   'ATENDIDA','CANCELADA','NO_ASISTIO')
        ),
        CONSTRAINT ck_cita_evento_estado_nuevo CHECK (
            estado_nuevo IS NULL
            OR estado_nuevo IN ('PROGRAMADA','CONFIRMADA','EN_ESPERA',
                                'ATENDIDA','CANCELADA','NO_ASISTIO')
        ),
        CONSTRAINT ck_cita_evento_creacion CHECK (
            tipo <> 'CITA_CREADA'
            OR (estado_anterior IS NULL AND fecha_anterior IS NULL
                AND hora_anterior IS NULL)
        ),
        CONSTRAINT ck_cita_evento_cambio_estado CHECK (
            tipo <> 'CITA_ESTADO_CAMBIADO'
            OR (estado_anterior IS NOT NULL AND estado_nuevo IS NOT NULL
                AND estado_anterior <> estado_nuevo)
        ),
        CONSTRAINT ck_cita_evento_reprogramacion CHECK (
            tipo <> 'CITA_REPROGRAMADA'
            OR (fecha_anterior IS NOT NULL AND hora_anterior IS NOT NULL
                AND fecha_nueva IS NOT NULL AND hora_nueva IS NOT NULL
                AND (fecha_anterior <> fecha_nueva
                     OR hora_anterior <> hora_nueva))
        )
    )""",
    """CREATE INDEX ix_cita_evento_cita_fecha
        ON cita_evento (cita_id, fecha_evento)""",
    """CREATE INDEX ix_cita_evento_tipo_fecha
        ON cita_evento (tipo, fecha_evento)""",
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
    """CREATE INDEX ix_dispositivo_push_revocacion_cola
        ON dispositivo_push_revocacion (estado, proximo_intento)""",
    """CREATE INDEX ix_dispositivo_push_revocacion_lease
        ON dispositivo_push_revocacion (bloqueado_hasta)
        WHERE estado = 'PROCESANDO'""",
    """CREATE INDEX ix_dispositivo_push_revocacion_usuario
        ON dispositivo_push_revocacion (usuario_id, estado)""",
)

DDL_CONTROL = """CREATE TABLE saas_control.dispositivo_push_registry (
    id INTEGER PRIMARY KEY,
    token_fingerprint VARCHAR(64) NOT NULL,
    tenant_database_id INTEGER NOT NULL
        REFERENCES tenant_database(id) ON DELETE RESTRICT,
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
    CONSTRAINT ck_dispositivo_push_registry_version CHECK (huella_version >= 1),
    CONSTRAINT ck_dispositivo_push_registry_revocacion CHECK (
        (estado = 'REVOCADO' AND fecha_revocacion IS NOT NULL)
        OR (estado <> 'REVOCADO' AND fecha_revocacion IS NULL)
    ),
    CONSTRAINT ck_dispositivo_push_registry_usuario CHECK (usuario_id > 0)
)"""


AHORA = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
TOKEN_A = "fcm-token-de-prueba-00000000000000000001"
TOKEN_B = "fcm-token-de-prueba-00000000000000000002"

DATOS_EVENTO = dict(
    cita_id=1,
    tipo="CITA_CREADA",
    clave_idempotencia="evt-cita-1-creacion",
)


def _motor_tenant():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in DDL_TENANT:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(
            "INSERT INTO usuario (id, correo) VALUES "
            "(1,'u1@example.test'),(2,'u2@example.test'),(7,'u7@example.test')"
        )
        conn.exec_driver_sql("INSERT INTO cita (id) VALUES (1),(2),(3)")
    return engine


@pytest.fixture
def engine():
    motor = _motor_tenant()
    try:
        yield motor
    finally:
        motor.dispose()


@pytest.fixture
def db(engine):
    with Session(engine, autoflush=False) as sesion:
        yield sesion


@pytest.fixture
def control_engine():
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    raw = motor.raw_connection()
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
    try:
        yield motor
    finally:
        motor.dispose()


@pytest.fixture
def control_db(control_engine):
    with Session(control_engine, autoflush=False) as sesion:
        yield sesion


def _dispositivo(db: Session, *, id_: int = 1, usuario_id: int = 1,
                 token: str = TOKEN_A, activo: bool = True) -> DispositivoPush:
    fila = DispositivoPush(
        id=id_,
        usuario_id=usuario_id,
        token_fcm=token,
        plataforma="ANDROID",
        activo=activo,
    )
    db.add(fila)
    db.flush()
    return fila


def _notificacion(db: Session, *, id_: int = 1, usuario_id: int = 1,
                  evento_clave: str = "evt-notif-1") -> Notificacion:
    fila = Notificacion(
        id=id_,
        usuario_id=usuario_id,
        tipo="CITA_CREADA",
        evento_clave=evento_clave,
        titulo="Cita registrada",
        cuerpo="Su cita fue registrada.",
        datos={"ruta": "/citas/1"},
        cita_id=1,
    )
    db.add(fila)
    db.flush()
    return fila


# --------------------------------------------------------------------------- #
# Modelos e importacion
# --------------------------------------------------------------------------- #
def test_modelos_importan_con_tablas_esperadas():
    assert DispositivoPush.__tablename__ == "dispositivo_push"
    assert Notificacion.__tablename__ == "notificacion"
    assert NotificacionEntrega.__tablename__ == "notificacion_entrega"
    assert PreferenciaNotificacion.__tablename__ == "preferencia_notificacion"
    assert CitaEvento.__tablename__ == "cita_evento"
    assert RevocacionPushGlobal.__tablename__ == "dispositivo_push_revocacion"
    assert DispositivoPushRegistry.__tablename__ == "dispositivo_push_registry"
    assert DispositivoPushRegistry.__table__.schema == "saas_control"

    columnas = set(DispositivoPushRegistry.__table__.columns.keys())
    assert "token_fingerprint" in columnas
    assert "token_fcm" not in columnas


def test_tablas_tenant_no_llevan_tenant_id():
    for tabla in TABLAS_TENANT:
        assert "tenant_id" not in tabla.columns
        assert "empresa_id" not in tabla.columns


# --------------------------------------------------------------------------- #
# Dispositivos y token
# --------------------------------------------------------------------------- #
def test_token_activo_es_unico_en_el_tenant(db):
    dispositivo = _dispositivo(db)
    db.commit()

    db.add(DispositivoPush(
        id=2, usuario_id=2, token_fcm=TOKEN_A, plataforma="IOS", activo=True,
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    # El mismo token puede reactivarse solo tras revocar el anterior.
    dispositivo = db.get(DispositivoPush, 1)
    dispositivo.activo = False
    dispositivo.revocado_en = AHORA
    db.commit()

    db.add(DispositivoPush(
        id=2, usuario_id=2, token_fcm=TOKEN_A, plataforma="IOS", activo=True,
    ))
    db.commit()
    assert db.scalar(select(func.count()).select_from(DispositivoPush)) == 2


def test_plataforma_invalida_es_rechazada(db):
    db.add(DispositivoPush(
        id=1, usuario_id=1, token_fcm=TOKEN_B, plataforma="DESKTOP",
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_indice_de_dispositivos_por_usuario(db):
    _dispositivo(db, id_=1, usuario_id=7, token=TOKEN_A)
    _dispositivo(db, id_=2, usuario_id=7, token=TOKEN_B)
    db.commit()

    activos = repo_dispositivos.listar_activos_por_usuario(db, 7)
    assert [item.id for item in activos] == [1, 2]


def test_repositorio_revoca_logicamente(db):
    dispositivo = _dispositivo(db)
    db.commit()

    repo_dispositivos.revocar(db, dispositivo, AHORA)
    db.commit()

    assert dispositivo.activo is False
    assert dispositivo.revocado_en is not None
    assert repo_dispositivos.listar_activos_por_usuario(db, 1) == []


# --------------------------------------------------------------------------- #
# Bandeja y entregas
# --------------------------------------------------------------------------- #
def test_evento_clave_de_notificacion_es_unico(db):
    _notificacion(db)
    db.commit()
    db.add(Notificacion(
        id=2, usuario_id=1, tipo="CITA_CONFIRMADA",
        evento_clave="evt-notif-1", titulo="Otra", cuerpo="Otra",
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_tipo_de_notificacion_invalido_es_rechazado(db):
    db.add(Notificacion(
        id=1, usuario_id=1, tipo="PROMOCION", evento_clave="evt-x",
        titulo="X", cuerpo="X",
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_lectura_incoherente_es_rechazada(db):
    db.add(Notificacion(
        id=1, usuario_id=1, tipo="CITA_CREADA", evento_clave="evt-x",
        titulo="X", cuerpo="X", leida=True, fecha_lectura=None,
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_entrega_unica_por_notificacion_y_dispositivo(db):
    _dispositivo(db)
    _notificacion(db)
    db.commit()

    repo_entregas.crear(db, NotificacionEntregaPersistencia(
        notificacion_id=1, dispositivo_id=1,
    ))
    db.commit()

    db.add(NotificacionEntrega(
        id=2, notificacion_id=1, dispositivo_id=1,
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_proveedor_de_entrega_solo_fcm(db):
    _dispositivo(db)
    _notificacion(db)
    db.commit()

    db.add(NotificacionEntrega(
        id=1, notificacion_id=1, dispositivo_id=1, proveedor="RESEND",
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_estado_inicial_y_cola_de_entregas(db):
    _dispositivo(db)
    _notificacion(db)
    db.flush()
    entregas = repo_entregas.crear_pendientes(db, 1, [1])
    db.commit()

    assert entregas[0].estado == "PENDIENTE"
    assert entregas[0].intentos == 0
    assert [item.id for item in repo_entregas.listar_pendientes(db)] == [1]


def test_bandeja_sin_dispositivos(db):
    _notificacion(db)
    db.commit()

    # La bandeja no depende de dispositivos ni de push.
    assert repo_notificaciones.listar_por_usuario(db, 1)[0].id == 1
    assert db.scalar(select(func.count()).select_from(DispositivoPush)) == 0


def test_marcar_leida_conserva_coherencia(db):
    notificacion = _notificacion(db)
    db.commit()

    repo_notificaciones.marcar_leida(db, notificacion, AHORA)
    db.commit()

    assert notificacion.leida is True
    assert notificacion.fecha_lectura is not None


# --------------------------------------------------------------------------- #
# Preferencias
# --------------------------------------------------------------------------- #
def test_preferencia_unica_por_usuario(db):
    preferencia = repo_preferencias.crear_por_defecto(db, 1)
    db.commit()
    assert preferencia.push_habilitado is True
    assert preferencia.recordatorios is True

    db.add(PreferenciaNotificacion(id=2, usuario_id=1))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_preferencia_actualiza_solo_campos_enviados(db):
    preferencia = repo_preferencias.crear_por_defecto(db, 1)
    db.commit()

    cambios = PreferenciaNotificacionActualizacion(
        push_habilitado=False,
    ).cambios()
    assert cambios == {"push_habilitado": False}

    repo_preferencias.aplicar_cambios(db, preferencia, cambios, AHORA)
    db.commit()

    assert preferencia.push_habilitado is False
    assert preferencia.citas_creadas is True


# --------------------------------------------------------------------------- #
# Eventos de cita
# --------------------------------------------------------------------------- #
def test_evento_de_creacion(db):
    evento = repo_eventos.registrar(db, CitaEventoPersistencia(**DATOS_EVENTO))
    db.commit()

    assert evento.tipo == "CITA_CREADA"
    assert evento.estado_anterior is None
    assert repo_eventos.listar_por_cita(db, 1)[0].id == evento.id


def test_evento_de_cambio_de_estado(db):
    evento = repo_eventos.registrar(db, CitaEventoPersistencia(
        cita_id=1,
        tipo="CITA_ESTADO_CAMBIADO",
        estado_anterior="PROGRAMADA",
        estado_nuevo="CONFIRMADA",
        clave_idempotencia="evt-cita-1-estado-confirmada",
    ))
    db.commit()

    assert (evento.estado_anterior, evento.estado_nuevo) == (
        "PROGRAMADA", "CONFIRMADA",
    )


def test_evento_de_reprogramacion_con_agenda_anterior_y_nueva(db):
    evento = repo_eventos.registrar(db, CitaEventoPersistencia(
        cita_id=1,
        tipo="CITA_REPROGRAMADA",
        fecha_anterior=date(2026, 11, 25),
        hora_anterior=time(9, 0),
        fecha_nueva=date(2026, 11, 27),
        hora_nueva=time(10, 30),
        clave_idempotencia="evt-cita-1-reprogramacion",
    ))
    db.commit()

    assert evento.fecha_anterior == date(2026, 11, 25)
    assert evento.hora_nueva == time(10, 30)


def test_reprogramacion_incompleta_falla_en_el_modelo(db):
    db.add(CitaEvento(
        id=1, cita_id=1, tipo="CITA_REPROGRAMADA",
        fecha_anterior=date(2026, 11, 25), hora_anterior=time(9, 0),
        fecha_nueva=date(2026, 11, 27), hora_nueva=None,
        clave_idempotencia="evt-incompleto",
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_cambio_de_estado_sin_cambio_real_falla(db):
    db.add(CitaEvento(
        id=1, cita_id=1, tipo="CITA_ESTADO_CAMBIADO",
        estado_anterior="PROGRAMADA", estado_nuevo="PROGRAMADA",
        clave_idempotencia="evt-sin-cambio",
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_evento_con_estado_de_cita_invalido_falla(db):
    db.add(CitaEvento(
        id=1, cita_id=1, tipo="CITA_ESTADO_CAMBIADO",
        estado_anterior="REPROGRAMADA", estado_nuevo="ATENDIDA",
        clave_idempotencia="evt-estado-invalido",
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_idempotencia_de_evento_no_duplica(db):
    datos = CitaEventoPersistencia(**DATOS_EVENTO)
    primero = repo_eventos.registrar(db, datos)
    segundo = repo_eventos.registrar(db, datos)
    db.commit()

    assert primero.id == segundo.id
    assert db.scalar(select(func.count()).select_from(CitaEvento)) == 1


def test_evento_duplicado_por_sql_tambien_falla(db):
    repo_eventos.registrar(db, CitaEventoPersistencia(**DATOS_EVENTO))
    db.commit()

    db.add(CitaEvento(
        id=99, cita_id=1, tipo="CITA_CREADA",
        clave_idempotencia="evt-cita-1-creacion",
    ))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --------------------------------------------------------------------------- #
# Aislamiento multitenant
# --------------------------------------------------------------------------- #
def test_identidad_de_usuario_aislada_por_tenant():
    motor_a = _motor_tenant()
    motor_b = _motor_tenant()
    try:
        with Session(motor_a) as db_a, Session(motor_b) as db_b:
            db_a.add(Notificacion(
                id=1, usuario_id=1, tipo="CITA_CREADA",
                evento_clave="evt-a", titulo="A", cuerpo="A",
            ))
            db_b.add(Notificacion(
                id=1, usuario_id=1, tipo="CITA_CREADA",
                evento_clave="evt-b", titulo="B", cuerpo="B",
            ))
            db_a.commit()
            db_b.commit()

            assert [n.evento_clave for n in repo_notificaciones.listar_por_usuario(
                db_a, 1,
            )] == ["evt-a"]
            assert [n.evento_clave for n in repo_notificaciones.listar_por_usuario(
                db_b, 1,
            )] == ["evt-b"]
    finally:
        motor_a.dispose()
        motor_b.dispose()


# --------------------------------------------------------------------------- #
# Registro global de huellas
# --------------------------------------------------------------------------- #
def test_huella_es_determinista_y_no_revela_el_token():
    huella = calcular_huella_token(TOKEN_A)
    assert huella == calcular_huella_token(TOKEN_A)
    assert huella != calcular_huella_token(TOKEN_B)
    assert len(huella) == 64
    assert TOKEN_A not in huella


def test_huella_unica_global_en_control_plane(control_db):
    registro = registro_global.registrar_propiedad(
        control_db,
        token_fcm=TOKEN_A,
        tenant_database_id=11,
        usuario_id=1,
    )
    control_db.commit()
    assert registro.estado == "ACTIVO"
    assert registro.token_fingerprint == calcular_huella_token(TOKEN_A)

    control_db.add(DispositivoPushRegistry(
        id=99,
        token_fingerprint=calcular_huella_token(TOKEN_A),
        tenant_database_id=22,
        usuario_id=1,
    ))
    with pytest.raises(IntegrityError):
        control_db.flush()
    control_db.rollback()


def test_registro_global_no_guarda_token_y_reutiliza_huella(control_db):
    primero = registro_global.registrar_propiedad(
        control_db,
        token_fcm=TOKEN_A,
        tenant_database_id=11,
        usuario_id=1,
    )
    segundo = registro_global.registrar_propiedad(
        control_db,
        token_fcm=TOKEN_A,
        tenant_database_id=22,
        usuario_id=1,
    )
    control_db.commit()

    assert primero.id == segundo.id
    assert primero.tenant_database_id == 11
    assert control_db.scalar(
        select(func.count()).select_from(DispositivoPushRegistry)
    ) == 1
    assert all(
        "token_fcm" != columna.name
        for columna in DispositivoPushRegistry.__table__.columns
    )


def test_id_local_se_interpreta_junto_al_tenant(control_db):
    registro_global.registrar_propiedad(
        control_db, token_fcm=TOKEN_A, tenant_database_id=11, usuario_id=1,
    )
    registro_global.registrar_propiedad(
        control_db, token_fcm=TOKEN_B, tenant_database_id=22, usuario_id=1,
    )
    control_db.commit()

    por_tenant = registro_global.listar_por_tenant(control_db, 11)
    assert len(por_tenant) == 1
    assert por_tenant[0].token_fingerprint == calcular_huella_token(TOKEN_A)


def test_revocacion_del_registro_global(control_db):
    registro = registro_global.registrar_propiedad(
        control_db, token_fcm=TOKEN_A, tenant_database_id=11, usuario_id=1,
    )
    control_db.commit()

    registro_global.revocar(control_db, registro, AHORA)
    control_db.commit()
    assert registro.estado == "REVOCADO"
    assert registro.fecha_revocacion is not None


def test_huella_invalida_es_rechazada_en_el_modelo(control_db):
    control_db.add(DispositivoPushRegistry(
        id=1,
        token_fingerprint="NO-ES-UNA-HUELLA",
        tenant_database_id=11,
        usuario_id=1,
    ))
    with pytest.raises(IntegrityError):
        control_db.flush()
    control_db.rollback()


# --------------------------------------------------------------------------- #
# Schemas: sin token y sin datos clinicos
# --------------------------------------------------------------------------- #
def test_schema_de_vista_no_expone_token():
    assert "token_fcm" not in DispositivoPushVista.model_fields
    assert "token_fcm" not in DispositivoPushVista(
        id=1,
        usuario_id=1,
        plataforma="ANDROID",
        activo=True,
        fecha_registro=AHORA,
        fecha_actualizacion=AHORA,
    ).model_dump()


def test_repr_de_schema_y_modelo_ocultan_token():
    datos = DispositivoPushPersistencia(
        usuario_id=1,
        token_fcm=TOKEN_A,
        plataforma="ANDROID",
    )
    assert TOKEN_A not in repr(datos)
    assert TOKEN_A not in str(datos)

    modelo = DispositivoPush(id=1, usuario_id=1, token_fcm=TOKEN_A,
                             plataforma="ANDROID", activo=True)
    assert TOKEN_A not in repr(modelo)
    assert TOKEN_A not in str(modelo)


def test_schema_rechaza_campos_no_declarados():
    with pytest.raises(ValidationError):
        NotificacionPersistencia(
            usuario_id=1,
            tipo="CITA_CREADA",
            evento_clave="evt-x",
            titulo="X",
            cuerpo="X",
            tenant_id=99,
        )


def test_schema_rechaza_datos_clinicos_en_notificacion():
    with pytest.raises(ValidationError):
        NotificacionPersistencia(
            usuario_id=1,
            tipo="CITA_CREADA",
            evento_clave="evt-x",
            titulo="X",
            cuerpo="X",
            datos={"diagnostico": "texto clinico"},
        )


def test_schema_de_evento_exige_reprogramacion_completa():
    with pytest.raises(ValidationError):
        CitaEventoPersistencia(
            cita_id=1,
            tipo="CITA_REPROGRAMADA",
            fecha_anterior=date(2026, 11, 25),
            hora_anterior=time(9, 0),
            fecha_nueva=date(2026, 11, 27),
            hora_nueva=None,
            clave_idempotencia="evt-incompleto",
        )


def test_schema_de_evento_exige_cambio_real_de_estado():
    with pytest.raises(ValidationError):
        CitaEventoPersistencia(
            cita_id=1,
            tipo="CITA_ESTADO_CAMBIADO",
            estado_anterior="PROGRAMADA",
            estado_nuevo="PROGRAMADA",
            clave_idempotencia="evt-sin-cambio",
        )


def test_schemas_de_vista_no_contienen_campos_clinicos():
    for modelo in (NotificacionVista, CitaEventoVista):
        campos = set(modelo.model_fields)
        assert not campos & {"diagnostico", "anamnesis", "motivo", "receta"}


# --------------------------------------------------------------------------- #
# Artefactos de migracion (estatico, sin ejecutar SQL)
# --------------------------------------------------------------------------- #
def test_manifiesto_integra_la_migracion_3c2():
    from app.modules.administracion_saas.tenant_migrator import load_bundle

    bundle = load_bundle(MIGRATION_ROOT)
    assert bundle.schema_version == "v9-sprint5-revocacion-global-push"
    cu3c2 = next(
        item for item in bundle.migrations if item.version == "20261121000000"
    )
    assert cu3c2.name == "cu3c2_notificaciones_push"
    assert cu3c2.checksum == (
        "4e0cb1de5fa9b6b13e5f22b8d3da372fbdeac2d5c2b58d7aff5efc90d22556d2"
    )
    cu3c52 = next(
        item for item in bundle.migrations if item.version == "20261123000000"
    )
    assert cu3c52.name == "cu3c52_revocacion_global_push"
    ultima = bundle.migrations[-1]
    assert ultima.version == "20261123000000"
    assert ultima.name == "cu3c52_revocacion_global_push"
    assert "v7-sprint5-notificaciones-push" in bundle.allowed_source_schema_versions
    assert "v8-sprint5-worker-fcm-push" in bundle.allowed_source_schema_versions
    nombres = [nombre for nombre, _ in bundle.supporting_checksums]
    assert nombres == [
        "bootstrap_ledger.sql",
        "000_preflight_cu3c5.sql",
        "999_verify_cu3c5.sql",
    ]


def test_migracion_tenant_declara_tablas_y_restricciones():
    sql = (
        MIGRATION_ROOT / "versions" / "20261121000000_cu3c2_notificaciones_push.sql"
    ).read_text(encoding="utf-8")

    for tabla in (
        "dispositivo_push", "notificacion", "notificacion_entrega",
        "preferencia_notificacion", "cita_evento",
    ):
        assert f"CREATE TABLE public.{tabla}" in sql
    assert "WHERE activo;" in sql
    assert "uq_notificacion_evento_clave" in sql
    assert "uq_notificacion_entrega_notificacion_dispositivo" in sql
    assert "uq_preferencia_notificacion_usuario" in sql
    assert "uq_cita_evento_clave_idempotencia" in sql
    assert "CITA_REPROGRAMADA" in sql
    # Transaccion controlada por el runner.
    assert not re.search(r"(?im)^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", sql)


def test_preflight_y_verify_son_de_solo_lectura():
    for nombre in (
        "000_preflight_cu3c2.sql", "999_verify_cu3c2.sql",
        "000_preflight_cu3c5.sql", "999_verify_cu3c5.sql",
    ):
        sql = (MIGRATION_ROOT / nombre).read_text(encoding="utf-8")
        assert "BEGIN TRANSACTION READ ONLY;" in sql
        assert sql.rstrip().endswith("ROLLBACK;")
        assert "CREATE TABLE" not in sql
        assert "ALTER TABLE" not in sql
        assert "GRANT " not in sql


def test_migracion_control_plane_no_contiene_token_en_claro():
    sql = (
        ROOT / "database" / "saas_control"
        / "009_create_dispositivo_push_registry.sql"
    ).read_text(encoding="utf-8")

    assert "saas_control.dispositivo_push_registry" in sql
    assert "token_fingerprint" in sql
    assert "token_fcm VARCHAR" not in sql
    assert "REFERENCES saas_control.tenant_database(id) ON DELETE RESTRICT" in sql
    assert "UNIQUE (token_fingerprint)" in sql
    assert "REVOKE ALL PRIVILEGES ON TABLE" in sql
    assert "CREATE TABLE public." not in sql


def test_nombres_de_constraints_del_modelo_coinciden_con_las_migraciones():
    def nombres(tabla) -> set[str]:
        return {
            elemento.name
            for elemento in (*tabla.constraints, *tabla.indexes)
            if elemento.name
        }

    sql_tenant = (
        MIGRATION_ROOT / "versions" / "20261121000000_cu3c2_notificaciones_push.sql"
    ).read_text(encoding="utf-8") + (
        MIGRATION_ROOT / "versions" / "20261122000000_cu3c5_worker_fcm_push.sql"
    ).read_text(encoding="utf-8") + (
        MIGRATION_ROOT
        / "versions" / "20261123000000_cu3c52_revocacion_global_push.sql"
    ).read_text(encoding="utf-8")
    for tabla in TABLAS_TENANT:
        faltantes = {nombre for nombre in nombres(tabla) if nombre not in sql_tenant}
        assert not faltantes, f"{tabla.name}: {sorted(faltantes)}"

    sql_control = (
        ROOT / "database" / "saas_control"
        / "009_create_dispositivo_push_registry.sql"
    ).read_text(encoding="utf-8")
    faltantes = {
        nombre
        for nombre in nombres(DispositivoPushRegistry.__table__)
        if nombre not in sql_control
    }
    assert not faltantes, sorted(faltantes)
