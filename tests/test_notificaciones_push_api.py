"""Fase 3C.3 - pruebas aisladas de la API de notificaciones push.

SQLite en memoria y Control Plane simulado con un esquema ``saas_control``
adjunto. No se abre ninguna conexion real, no se aplican migraciones y no se
llama a FCM/APNs. El bloqueo tenant se prueba con JWT firmados reales emitidos
por ``app.core.security``; no se leen secretos.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies as core_dependencies
from app.core.security import (
    crear_access_token,
    crear_saas_access_token,
    crear_tenant_access_token,
)
from app.main import app
from app.modules.notificaciones_push.api import dependencies as push_dependencies
from app.modules.notificaciones_push.models.models import (
    DispositivoPush,
    DispositivoPushRegistry,
    Notificacion,
    NotificacionEntrega,
    PreferenciaNotificacion,
)
from app.modules.notificaciones_push.schemas.http import (
    DispositivoRespuesta,
    RegistroDispositivoRequest,
    filtrar_datos_navegacion,
)
from app.modules.notificaciones_push.schemas.schemas import (
    DispositivoPushPersistencia,
)
from app.modules.notificaciones_push.seguridad import calcular_huella_token
from app.modules.notificaciones_push.services import (
    dispositivos as servicio_dispositivos,
)

TOKEN_A = "fcm-token-de-prueba-00000000000000000001"
TOKEN_B = "fcm-token-de-prueba-00000000000000000002"
TOKEN_C = "fcm-token-de-prueba-00000000000000000003"

TENANT_A = 11
TENANT_B = 22

DDL_TENANT = (
    """CREATE TABLE rol (
        id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT,
        estado BOOLEAN NOT NULL, protegido BOOLEAN NOT NULL,
        fecha_creacion TIMESTAMP NOT NULL
    )""",
    """CREATE TABLE usuario (
        id INTEGER PRIMARY KEY, correo TEXT NOT NULL, password_hash TEXT NOT NULL,
        estado BOOLEAN NOT NULL, fecha_creacion TIMESTAMP NOT NULL,
        rol_id INTEGER NOT NULL REFERENCES rol(id)
    )""",
    "CREATE TABLE cita (id INTEGER PRIMARY KEY)",
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


def _motor_tenant() -> object:
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
            "INSERT INTO rol (id, nombre, estado, protegido, fecha_creacion) "
            "VALUES (1,'Paciente',1,0,'2026-01-01'), "
            "(2,'Administrador',1,0,'2026-01-01')"
        )
        conn.exec_driver_sql(
            "INSERT INTO usuario (id, correo, password_hash, estado, "
            "fecha_creacion, rol_id) VALUES "
            "(1,'paciente1@example.test','x',1,'2026-01-01',1),"
            "(2,'paciente2@example.test','x',1,'2026-01-01',1),"
            "(3,'admin@example.test','x',1,'2026-01-01',2),"
            "(4,'inactivo@example.test','x',0,'2026-01-01',1),"
            "(5,'paciente5@example.test','x',1,'2026-01-01',1)"
        )
        conn.exec_driver_sql("INSERT INTO cita (id) VALUES (1),(2),(3)")
    return engine


@pytest.fixture
def motor_a():
    engine = _motor_tenant()
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def motor_b():
    engine = _motor_tenant()
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_a(motor_a):
    with Session(motor_a, autoflush=False) as sesion:
        sesion.info["tenant_context"] = SimpleNamespace(
            tenant_database_id=TENANT_A,
        )
        yield sesion


@pytest.fixture
def db_b(motor_b):
    with Session(motor_b, autoflush=False) as sesion:
        sesion.info["tenant_context"] = SimpleNamespace(
            tenant_database_id=TENANT_B,
        )
        yield sesion


@pytest.fixture
def motor_control():
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
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def control_db(motor_control):
    with Session(motor_control, autoflush=False) as sesion:
        yield sesion


@contextmanager
def cliente(
    db: Session,
    control: Session,
    *,
    tenant_id: int = TENANT_A,
    usuario_id: int = 1,
    rol_id: int = 1,
    token: str | None = None,
):
    def _db():
        yield db

    def _control():
        yield control

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[core_dependencies.get_db] = _db
    app.dependency_overrides[push_dependencies.get_control_plane_db] = _control
    try:
        with TestClient(app) as client:
            if token is None:
                token = crear_tenant_access_token(
                    usuario_id=usuario_id,
                    rol_id=rol_id,
                    tenant_id=tenant_id,
                    empresa_id=100 + tenant_id,
                    empresa_codigo=f"CLINICA-{tenant_id}",
                    rol_nombre="Paciente" if rol_id == 1 else "Administrador",
                )
            client.headers["Authorization"] = f"Bearer {token}"
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)


def _cuerpo(token: str = TOKEN_A, plataforma: str = "ANDROID") -> dict:
    return {"token_fcm": token, "plataforma": plataforma}


def _registro_control(control: Session, token: str) -> DispositivoPushRegistry | None:
    return control.scalar(
        select(DispositivoPushRegistry).where(
            DispositivoPushRegistry.token_fingerprint
            == calcular_huella_token(token)
        )
    )


def _notificacion(
    db: Session,
    *,
    id_: int,
    usuario_id: int = 1,
    evento_clave: str,
    fecha: datetime | None = None,
    datos: dict | None = None,
) -> Notificacion:
    fila = Notificacion(
        id=id_,
        usuario_id=usuario_id,
        tipo="CITA_CREADA",
        evento_clave=evento_clave,
        titulo="Cita registrada",
        cuerpo="Su cita fue registrada.",
        datos=datos,
        fecha_creacion=fecha or AHORA,
        fecha_actualizacion=fecha or AHORA,
    )
    db.add(fila)
    db.flush()
    return fila


# --------------------------------------------------------------------------- #
# Puerta tenant
# --------------------------------------------------------------------------- #
def test_jwt_tenant_valido_accede(db_a, control_db):
    with cliente(db_a, control_db) as client:
        respuesta = client.get("/notificaciones/preferencias")
    assert respuesta.status_code == 200
    assert respuesta.json()["push_habilitado"] is True


def test_jwt_legacy_rechazado_sin_fallback(db_a, control_db):
    with cliente(
        db_a, control_db, token=crear_access_token(1, 1),
    ) as client:
        respuesta = client.get("/notificaciones")
    assert respuesta.status_code == 401
    assert respuesta.json()["detail"] == "Token inválido o expirado"


def test_jwt_saas_rechazado(db_a, control_db):
    with cliente(
        db_a, control_db, token=crear_saas_access_token(1),
    ) as client:
        respuesta = client.get("/notificaciones")
    assert respuesta.status_code == 401


def test_token_manipulado_rechazado(db_a, control_db):
    token = crear_tenant_access_token(
        usuario_id=1, rol_id=1, tenant_id=TENANT_A,
        empresa_id=111, empresa_codigo="CLINICA-11",
    )
    with cliente(db_a, control_db, token=token[:-2] + "xx") as client:
        respuesta = client.get("/notificaciones")
    assert respuesta.status_code == 401


def test_claim_tenant_cruzado_rechazado(db_a, control_db):
    token = crear_tenant_access_token(
        usuario_id=1, rol_id=1, tenant_id=TENANT_B,
        empresa_id=122, empresa_codigo="CLINICA-22",
    )
    with cliente(db_a, control_db, token=token) as client:
        respuesta = client.get("/notificaciones")
    assert respuesta.status_code == 401
    assert "no coincide" in respuesta.json()["detail"]


def test_contexto_tenant_ausente_falla_cerrado(db_a, control_db):
    contexto = db_a.info.pop("tenant_context")
    try:
        with cliente(db_a, control_db) as client:
            respuesta = client.get("/notificaciones")
    finally:
        db_a.info["tenant_context"] = contexto
    assert respuesta.status_code == 503


def test_usuario_inactivo_rechazado(db_a, control_db):
    with cliente(db_a, control_db, usuario_id=4) as client:
        respuesta = client.get("/notificaciones")
    assert respuesta.status_code == 401


def test_rol_no_paciente_rechazado(db_a, control_db):
    with cliente(db_a, control_db, usuario_id=3, rol_id=2) as client:
        respuesta = client.get("/notificaciones")
    assert respuesta.status_code == 403


def test_ids_locales_iguales_aislados_entre_tenants(
    db_a, db_b, control_db,
):
    _notificacion(db_a, id_=1, evento_clave="evt-a")
    _notificacion(db_b, id_=1, evento_clave="evt-b")
    db_a.commit()
    db_b.commit()

    with cliente(db_a, control_db) as client:
        respuesta_a = client.get("/notificaciones")
    with cliente(db_b, control_db, tenant_id=TENANT_B) as client:
        respuesta_b = client.get("/notificaciones")

    assert [i["id"] for i in respuesta_a.json()["items"]] == [1]
    assert respuesta_a.json()["items"][0]["titulo"] == "Cita registrada"
    assert [i["id"] for i in respuesta_b.json()["items"]] == [1]
    # Mismo ID local, distinta base: no se mezclan filas.
    assert respuesta_a.json()["items"][0]["id"] == 1
    assert respuesta_b.json()["items"][0]["id"] == 1


# --------------------------------------------------------------------------- #
# Registro de dispositivos
# --------------------------------------------------------------------------- #
def test_registro_exitoso_y_registro_global_activo(db_a, control_db):
    with cliente(db_a, control_db) as client:
        respuesta = client.post("/notificaciones/dispositivos", json=_cuerpo())

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["activo"] is True
    assert cuerpo["plataforma"] == "ANDROID"
    assert TOKEN_A not in respuesta.text
    assert "token" not in cuerpo

    assert db_a.scalar(
        select(func.count()).select_from(DispositivoPush)
    ) == 1
    registro = _registro_control(control_db, TOKEN_A)
    assert registro.estado == "ACTIVO"
    assert registro.tenant_database_id == TENANT_A


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("usuario_id", 99),
        ("paciente_id", 99),
        ("tenant_id", 22),
        ("empresa_id", 22),
        ("empresa_codigo", "CLINICA-22"),
        ("database_name", "tenant_22"),
        ("token_fingerprint", "a" * 64),
        ("activo", True),
        ("dsn", "postgresql://no-permitido"),
    ],
)
def test_campos_prohibidos_en_registro(
    db_a, control_db, campo, valor,
):
    cuerpo = _cuerpo()
    cuerpo[campo] = valor
    with cliente(db_a, control_db) as client:
        respuesta = client.post("/notificaciones/dispositivos", json=cuerpo)

    assert respuesta.status_code == 422
    assert TOKEN_A not in respuesta.text
    assert db_a.scalar(
        select(func.count()).select_from(DispositivoPush)
    ) == 0
    assert control_db.scalar(
        select(func.count()).select_from(DispositivoPushRegistry)
    ) == 0


def test_token_invalido_no_se_refleja(db_a, control_db):
    token_corto = "SECRETO-TOKEN-CORTO"
    with cliente(db_a, control_db) as client:
        respuesta = client.post(
            "/notificaciones/dispositivos",
            json={"token_fcm": token_corto, "plataforma": "ANDROID"},
        )
    assert respuesta.status_code == 422
    assert token_corto not in respuesta.text
    assert respuesta.json()["detail"] == "Token FCM inválido"


def test_plataforma_invalida_rechazada(db_a, control_db):
    with cliente(db_a, control_db) as client:
        respuesta = client.post(
            "/notificaciones/dispositivos",
            json={"token_fcm": TOKEN_A, "plataforma": "DESKTOP"},
        )
    assert respuesta.status_code == 422


def test_registro_idempotente_mismo_usuario(db_a, control_db):
    with cliente(db_a, control_db) as client:
        primero = client.post("/notificaciones/dispositivos", json=_cuerpo())
        segundo = client.post(
            "/notificaciones/dispositivos",
            json=_cuerpo(plataforma="IOS"),
        )

    assert primero.status_code == segundo.status_code == 200
    assert primero.json()["id"] == segundo.json()["id"]
    assert segundo.json()["plataforma"] == "IOS"
    assert db_a.scalar(
        select(func.count()).select_from(DispositivoPush)
    ) == 1
    assert control_db.scalar(
        select(func.count()).select_from(DispositivoPushRegistry)
    ) == 1


def test_token_activo_de_otro_tenant_bloqueado(db_a, control_db):
    control_db.add(DispositivoPushRegistry(
        id=1,
        token_fingerprint=calcular_huella_token(TOKEN_A),
        tenant_database_id=TENANT_B,
        usuario_id=1,
        estado="ACTIVO",
    ))
    control_db.commit()

    with cliente(db_a, control_db) as client:
        respuesta = client.post("/notificaciones/dispositivos", json=_cuerpo())

    assert respuesta.status_code == 409
    assert db_a.scalar(
        select(func.count()).select_from(DispositivoPush)
    ) == 0
    registro = _registro_control(control_db, TOKEN_A)
    assert registro.tenant_database_id == TENANT_B
    assert registro.estado == "ACTIVO"


def test_token_pendiente_de_otro_usuario_bloqueado(db_a, control_db):
    control_db.add(DispositivoPushRegistry(
        id=1,
        token_fingerprint=calcular_huella_token(TOKEN_A),
        tenant_database_id=TENANT_A,
        usuario_id=2,
        estado="PENDIENTE",
    ))
    control_db.commit()

    with cliente(db_a, control_db) as client:
        respuesta = client.post("/notificaciones/dispositivos", json=_cuerpo())

    assert respuesta.status_code == 409
    assert db_a.scalar(
        select(func.count()).select_from(DispositivoPush)
    ) == 0


def test_colision_concurrente_mismo_propietario_se_resuelve(
    db_a, control_db,
):
    # Simula que otra peticion del mismo usuario gano la reserva PENDIENTE.
    control_db.add(DispositivoPushRegistry(
        id=1,
        token_fingerprint=calcular_huella_token(TOKEN_A),
        tenant_database_id=TENANT_A,
        usuario_id=1,
        estado="PENDIENTE",
    ))
    control_db.commit()

    with cliente(db_a, control_db) as client:
        respuesta = client.post("/notificaciones/dispositivos", json=_cuerpo())

    assert respuesta.status_code == 200
    assert _registro_control(control_db, TOKEN_A).estado == "ACTIVO"


def test_fallo_tenant_compensa_reserva_pendiente(
    db_a, control_db, monkeypatch,
):
    def fallar(*args, **kwargs):
        raise SQLAlchemyError("fallo simulado de base tenant")

    monkeypatch.setattr(
        servicio_dispositivos, "_persistir_dispositivo", fallar,
    )
    with cliente(db_a, control_db) as client:
        respuesta = client.post("/notificaciones/dispositivos", json=_cuerpo())

    assert respuesta.status_code == 503
    assert db_a.scalar(
        select(func.count()).select_from(DispositivoPush)
    ) == 0
    assert _registro_control(control_db, TOKEN_A).estado == "REVOCADO"


def test_fallo_de_confirmacion_es_recuperable(
    db_a, control_db, monkeypatch,
):
    original = servicio_dispositivos._confirmar_huella
    llamadas = {"n": 0}

    def fallar_una_vez(control, registro, ahora):
        if llamadas["n"] == 0:
            llamadas["n"] += 1
            raise SQLAlchemyError("fallo simulado de control plane")
        return original(control, registro, ahora)

    monkeypatch.setattr(
        servicio_dispositivos, "_confirmar_huella", fallar_una_vez,
    )
    with cliente(db_a, control_db) as client:
        primera = client.post("/notificaciones/dispositivos", json=_cuerpo())
        assert primera.status_code == 503
        # El dispositivo quedo persistido y la huella PENDIENTE.
        assert db_a.scalar(
            select(func.count()).select_from(DispositivoPush)
        ) == 1
        assert _registro_control(control_db, TOKEN_A).estado == "PENDIENTE"

        segunda = client.post("/notificaciones/dispositivos", json=_cuerpo())
        assert segunda.status_code == 200

    assert control_db.scalar(
        select(func.count()).select_from(DispositivoPushRegistry)
    ) == 1
    assert _registro_control(control_db, TOKEN_A).estado == "ACTIVO"


def test_fallo_de_control_plane_en_reserva_no_toca_tenant(
    db_a, control_db, monkeypatch,
):
    def fallar(*args, **kwargs):
        raise SQLAlchemyError("control plane no disponible")

    monkeypatch.setattr(
        servicio_dispositivos.repo_registro, "obtener_por_huella", fallar,
    )
    with cliente(db_a, control_db) as client:
        respuesta = client.post("/notificaciones/dispositivos", json=_cuerpo())

    assert respuesta.status_code == 503
    assert db_a.scalar(
        select(func.count()).select_from(DispositivoPush)
    ) == 0


def test_token_no_aparece_en_logs(db_a, control_db, caplog):
    caplog.set_level(logging.DEBUG)
    huella = calcular_huella_token(TOKEN_A)
    with cliente(db_a, control_db) as client:
        client.post("/notificaciones/dispositivos", json=_cuerpo())
        client.get("/notificaciones")
    assert TOKEN_A not in caplog.text
    assert huella not in caplog.text


# --------------------------------------------------------------------------- #
# Revocacion
# --------------------------------------------------------------------------- #
def test_revocacion_propia_idempotente(db_a, control_db):
    with cliente(db_a, control_db) as client:
        creado = client.post("/notificaciones/dispositivos", json=_cuerpo())
        dispositivo_id = creado.json()["id"]

        primera = client.delete(f"/notificaciones/dispositivos/{dispositivo_id}")
        segunda = client.delete(f"/notificaciones/dispositivos/{dispositivo_id}")

    assert primera.status_code == 204
    assert segunda.status_code == 204
    dispositivo = db_a.get(DispositivoPush, dispositivo_id)
    assert dispositivo.activo is False
    assert dispositivo.revocado_en is not None
    assert _registro_control(control_db, TOKEN_A).estado == "REVOCADO"


def test_revocacion_ajena_no_revela_existencia(db_a, control_db):
    dispositivo = DispositivoPush(
        id=1, usuario_id=2, token_fcm=TOKEN_B,
        plataforma="ANDROID", activo=True,
    )
    db_a.add(dispositivo)
    db_a.commit()
    control_db.add(DispositivoPushRegistry(
        id=1,
        token_fingerprint=calcular_huella_token(TOKEN_B),
        tenant_database_id=TENANT_A,
        usuario_id=2,
        estado="ACTIVO",
    ))
    control_db.commit()

    with cliente(db_a, control_db) as client:
        ajeno = client.delete("/notificaciones/dispositivos/1")
    assert ajeno.status_code == 404
    assert ajeno.json()["detail"] == "Recurso no encontrado"

    assert db_a.get(DispositivoPush, 1).activo is True
    assert _registro_control(control_db, TOKEN_B).estado == "ACTIVO"


def test_revocacion_inexistente_mismo_mensaje(db_a, control_db):
    with cliente(db_a, control_db) as client:
        respuesta = client.delete("/notificaciones/dispositivos/999")
    assert respuesta.status_code == 404
    assert respuesta.json()["detail"] == "Recurso no encontrado"


def test_revocacion_conserva_entregas(db_a, control_db):
    with cliente(db_a, control_db) as client:
        creado = client.post("/notificaciones/dispositivos", json=_cuerpo())
        dispositivo_id = creado.json()["id"]

        _notificacion(db_a, id_=1, evento_clave="evt-entrega")
        db_a.add(NotificacionEntrega(
            id=1, notificacion_id=1, dispositivo_id=dispositivo_id,
        ))
        db_a.commit()

        respuesta = client.delete(f"/notificaciones/dispositivos/{dispositivo_id}")

    assert respuesta.status_code == 204
    assert db_a.scalar(
        select(func.count()).select_from(NotificacionEntrega)
    ) == 1


def test_fallo_global_en_revocacion_es_recuperable(
    db_a, control_db, monkeypatch,
):
    with cliente(db_a, control_db) as client:
        creado = client.post("/notificaciones/dispositivos", json=_cuerpo())
        dispositivo_id = creado.json()["id"]

        original = servicio_dispositivos.repo_registro.obtener_por_huella

        def fallar(*args, **kwargs):
            raise SQLAlchemyError("control plane no disponible")

        monkeypatch.setattr(
            servicio_dispositivos.repo_registro, "obtener_por_huella", fallar,
        )
        respuesta = client.delete(
            f"/notificaciones/dispositivos/{dispositivo_id}",
        )
        assert respuesta.status_code == 503
        assert db_a.get(DispositivoPush, dispositivo_id).activo is False
        # La huella sigue retenida: fail-closed.
        registro = control_db.scalar(select(DispositivoPushRegistry).where(
            DispositivoPushRegistry.token_fingerprint
            == calcular_huella_token(TOKEN_A)
        ))
        assert registro.estado == "ACTIVO"

        monkeypatch.setattr(
            servicio_dispositivos.repo_registro,
            "obtener_por_huella",
            original,
        )
        reintento = client.delete(
            f"/notificaciones/dispositivos/{dispositivo_id}",
        )
        assert reintento.status_code == 204

    assert _registro_control(control_db, TOKEN_A).estado == "REVOCADO"


# --------------------------------------------------------------------------- #
# Bandeja
# --------------------------------------------------------------------------- #
def test_bandeja_solo_propias(db_a, control_db):
    _notificacion(db_a, id_=1, usuario_id=1, evento_clave="evt-1")
    _notificacion(db_a, id_=2, usuario_id=2, evento_clave="evt-2")
    db_a.commit()

    with cliente(db_a, control_db) as client:
        respuesta = client.get("/notificaciones")

    assert respuesta.status_code == 200
    ids = [item["id"] for item in respuesta.json()["items"]]
    assert ids == [1]
    assert respuesta.json()["total"] == 1


def test_bandeja_paginacion_y_orden_estable(db_a, control_db):
    for indice in range(3):
        _notificacion(
            db_a, id_=indice + 1, evento_clave=f"evt-{indice}",
            fecha=AHORA + timedelta(minutes=indice),
        )
    db_a.commit()

    with cliente(db_a, control_db) as client:
        primera = client.get("/notificaciones?page=1&page_size=2")
        segunda = client.get("/notificaciones?page=2&page_size=2")

    assert primera.status_code == 200
    assert [i["id"] for i in primera.json()["items"]] == [3, 2]
    assert [i["id"] for i in segunda.json()["items"]] == [1]
    assert primera.json()["total"] == 3
    assert primera.json()["total_pages"] == 2


@pytest.mark.parametrize("page_size", [0, 51])
def test_bandeja_limite_de_pagina(db_a, control_db, page_size):
    with cliente(db_a, control_db) as client:
        respuesta = client.get(f"/notificaciones?page_size={page_size}")
    assert respuesta.status_code == 422


def test_bandeja_filtra_datos_por_allowlist(db_a, control_db):
    _notificacion(
        db_a, id_=1, evento_clave="evt-datos",
        datos={
            "ruta": "/citas/1",
            "cita_id": 1,
            "diagnostico": "texto clinico",
            "motivo": "dolor",
            "anidado": {"secreto": "x"},
            "otra_clave": "x",
        },
    )
    db_a.commit()

    with cliente(db_a, control_db) as client:
        respuesta = client.get("/notificaciones")

    datos = respuesta.json()["items"][0]["datos"]
    assert datos == {"ruta": "/citas/1", "cita_id": 1}
    assert "texto clinico" not in respuesta.text
    assert "dolor" not in respuesta.text


def test_filtro_de_datos_sin_claves_devuelve_none():
    assert filtrar_datos_navegacion({"diagnostico": "x"}) is None
    assert filtrar_datos_navegacion("no-es-dict") is None


def test_notificacion_ajena_no_se_marca(db_a, control_db):
    _notificacion(db_a, id_=1, usuario_id=2, evento_clave="evt-ajena")
    db_a.commit()

    with cliente(db_a, control_db) as client:
        respuesta = client.patch("/notificaciones/1/leida")

    assert respuesta.status_code == 404
    assert db_a.get(Notificacion, 1).leida is False


def test_marcar_leida_es_idempotente(db_a, control_db):
    _notificacion(db_a, id_=1, evento_clave="evt-leida")
    db_a.commit()

    with cliente(db_a, control_db) as client:
        primera = client.patch("/notificaciones/1/leida")
        segunda = client.patch("/notificaciones/1/leida")

    assert primera.status_code == segunda.status_code == 200
    assert primera.json()["leida"] is True
    assert segunda.json()["fecha_lectura"] == primera.json()["fecha_lectura"]
    assert db_a.get(Notificacion, 1).leida is True


# --------------------------------------------------------------------------- #
# Preferencias
# --------------------------------------------------------------------------- #
def test_preferencias_por_defecto_sin_escribir(db_a, control_db):
    with cliente(db_a, control_db) as client:
        respuesta = client.get("/notificaciones/preferencias")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["push_habilitado"] is True
    assert cuerpo["recordatorios"] is True
    assert cuerpo["fecha_actualizacion"] is None
    assert db_a.scalar(
        select(func.count()).select_from(PreferenciaNotificacion)
    ) == 0


@pytest.mark.parametrize("valor", ["true", "false", 1, 0, "si"])
def test_booleanos_estrictos_en_preferencias(db_a, control_db, valor):
    with cliente(db_a, control_db) as client:
        respuesta = client.put(
            "/notificaciones/preferencias",
            json={"push_habilitado": valor},
        )
    assert respuesta.status_code == 422
    assert db_a.scalar(
        select(func.count()).select_from(PreferenciaNotificacion)
    ) == 0


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("usuario_id", 2),
        ("tenant_id", 22),
        ("empresa_id", 22),
        ("token_fcm", TOKEN_A),
    ],
)
def test_preferencias_rechaza_campos_extra(db_a, control_db, campo, valor):
    with cliente(db_a, control_db) as client:
        respuesta = client.put(
            "/notificaciones/preferencias", json={campo: valor},
        )
    assert respuesta.status_code == 422


def test_preferencias_actualiza_solo_enviados(db_a, control_db):
    with cliente(db_a, control_db) as client:
        respuesta = client.put(
            "/notificaciones/preferencias",
            json={"push_habilitado": False},
        )
        consulta = client.get("/notificaciones/preferencias")

    assert respuesta.status_code == 200
    assert respuesta.json()["push_habilitado"] is False
    assert respuesta.json()["citas_creadas"] is True
    assert consulta.json()["push_habilitado"] is False
    fila = db_a.scalar(select(PreferenciaNotificacion))
    assert fila.usuario_id == 1


def test_desactivar_push_no_borra_bandeja(db_a, control_db):
    _notificacion(db_a, id_=1, evento_clave="evt-previa")
    db_a.commit()

    with cliente(db_a, control_db) as client:
        client.put(
            "/notificaciones/preferencias",
            json={"push_habilitado": False, "recordatorios": False},
        )
        respuesta = client.get("/notificaciones")

    assert respuesta.status_code == 200
    assert [item["id"] for item in respuesta.json()["items"]] == [1]
    assert db_a.get(Notificacion, 1) is not None


# --------------------------------------------------------------------------- #
# OpenAPI y privacidad de schemas
# --------------------------------------------------------------------------- #
def test_openapi_solo_expone_las_rutas_esperadas():
    esquema = app.openapi()
    rutas = {
        ruta: set(operaciones)
        for ruta, operaciones in esquema["paths"].items()
        if ruta.startswith("/notificaciones")
    }
    assert rutas == {
        "/notificaciones": {"get"},
        "/notificaciones/dispositivos": {"post"},
        "/notificaciones/dispositivos/{dispositivo_id}": {"delete"},
        "/notificaciones/{notificacion_id}/leida": {"patch"},
        "/notificaciones/preferencias": {"get", "put"},
    }


def test_openapi_no_expone_token_ni_campos_prohibidos():
    esquema = app.openapi()
    registro = esquema["components"]["schemas"]["RegistroDispositivoRequest"]
    propiedades = set(registro["properties"])
    assert "token_fcm" in propiedades
    for prohibido in (
        "usuario_id", "paciente_id", "tenant_id", "empresa_id",
        "empresa_codigo", "database_name", "token_fingerprint", "activo",
    ):
        assert prohibido not in propiedades
    assert registro.get("additionalProperties") is False

    respuesta = esquema["components"]["schemas"]["DispositivoRespuesta"]
    assert "token_fcm" not in respuesta["properties"]
    assert "token_fingerprint" not in respuesta["properties"]


def test_schemas_no_filtran_token():
    datos = RegistroDispositivoRequest(
        token_fcm=TOKEN_A, plataforma="ANDROID",
    )
    assert TOKEN_A not in repr(datos)
    assert TOKEN_A not in str(datos)
    assert "token_fcm" not in DispositivoRespuesta.model_fields
    # El schema de persistencia es interno: puede llevar el token, pero su
    # representacion no lo imprime.
    interno = DispositivoPushPersistencia(
        usuario_id=1, token_fcm=TOKEN_A, plataforma="ANDROID",
    )
    assert TOKEN_A not in repr(interno)
