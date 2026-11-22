# -*- coding: utf-8 -*-
"""Fase 3C.5 - fanout de entregas y worker FCM HTTP v1.

Todo es aislado: SQLite en memoria, reloj inyectado, proveedor de access token
falso y transporte HTTP simulado. No se leen secretos, no se aplican
migraciones, no se abren conexiones reales y no se llama a Firebase/FCM.

Limitacion declarada: SQLite **no** valida la concurrencia real de PostgreSQL
(``SELECT ... FOR UPDATE SKIP LOCKED``); esa validacion queda pendiente en un
entorno PostgreSQL aislado y autorizado.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.notificaciones_push.models.models import (
    CitaEvento,
    DispositivoPush,
    Notificacion,
    NotificacionEntrega,
    PreferenciaNotificacion,
)
from app.modules.notificaciones_push.providers import fcm
from app.modules.notificaciones_push.repositories import (
    entregas as repo_entregas,
)
from app.modules.notificaciones_push.services import entregas as servicio_entregas
from app.modules.notificaciones_push.services import (
    eventos_cita as servicio_eventos,
)
from app.modules.notificaciones_push.services import worker_fcm
from app.modules.notificaciones_push.services.worker_fcm import (
    ConfiguracionWorker,
    procesar_entregas,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "database" / "tenant_migrations"

AHORA = datetime(2026, 11, 21, 12, 0, 0)
TOKEN_A = "token-fcm-android-aaaaaaaaaaaa"
TOKEN_B = "token-fcm-ios-bbbbbbbbbbbbbbbbbb"
TOKEN_C = "token-fcm-web-cccccccccccccccccc"
TOKEN_AJENO = "token-fcm-ajeno-dddddddddddddd"
ACCESS_TOKEN = "oauth-access-token-opaco-no-real"

CLAVES_DATOS_PERMITIDAS = {"ruta", "pantalla", "seccion", "accion", "cita_id"}

DDL = (
    "CREATE TABLE usuario (id INTEGER PRIMARY KEY, estado BOOLEAN NOT NULL)",
    "CREATE TABLE cita (id INTEGER PRIMARY KEY)",
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
        cita_id INTEGER REFERENCES cita(id),
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
        )
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
    """CREATE TABLE cita_evento (
        id INTEGER PRIMARY KEY,
        cita_id INTEGER NOT NULL REFERENCES cita(id),
        tipo VARCHAR(30) NOT NULL,
        estado_anterior VARCHAR(20),
        estado_nuevo VARCHAR(20),
        fecha_anterior DATE,
        hora_anterior TIME,
        fecha_nueva DATE,
        hora_nueva TIME,
        fecha_evento TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actor_usuario_id INTEGER REFERENCES usuario(id),
        clave_idempotencia VARCHAR(150) NOT NULL,
        CONSTRAINT uq_cita_evento_clave_idempotencia UNIQUE (clave_idempotencia)
    )""",
    """CREATE TABLE dispositivo_push_revocacion (
        id INTEGER PRIMARY KEY,
        dispositivo_id INTEGER NOT NULL REFERENCES dispositivo_push(id),
        usuario_id INTEGER NOT NULL REFERENCES usuario(id),
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
        CONSTRAINT ck_dispositivo_push_revocacion_estado CHECK (
            estado IN ('PENDIENTE','PROCESANDO','COMPLETADA','NO_ENCONTRADO',
                       'RECHAZADA','FALLIDA')
        )
    )""",
)


# --------------------------------------------------------------------------- #
# Infra falsa
# --------------------------------------------------------------------------- #
class CredencialesFijas:
    """Proveedor de access token inyectado (valor ficticio, no es un secreto)."""

    def __init__(self, token: str = ACCESS_TOKEN) -> None:
        self._token = token

    def access_token(self) -> str:
        return self._token


class RespuestaFalsa:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.headers = dict(headers or {})

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class ClienteFalso:
    """Transporte HTTP simulado: guarda la solicitud y devuelve respuestas."""

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.llamadas: list[dict] = []

    def post(self, url, *, headers, json, timeout):
        self.llamadas.append({
            "url": url,
            "headers": dict(headers),
            "json": json,
            "timeout": timeout,
        })
        respuesta = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


def _proveedor(cliente: ClienteFalso) -> fcm.FcmHttpV1:
    return fcm.FcmHttpV1(
        proyecto_id="clinica-oftalmologica-demo",
        credenciales=CredencialesFijas(),
        cliente=cliente,
    )


def _aceptado(nombre: str = "projects/demo/messages/1") -> RespuestaFalsa:
    return RespuestaFalsa(200, {"name": nombre})


def _error(status: int, *, error_code: str | None = None,
           estado_error: str = "INVALID_ARGUMENT", headers=None) -> RespuestaFalsa:
    detalle = {"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError"}
    if error_code:
        detalle["errorCode"] = error_code
    return RespuestaFalsa(
        status,
        {"error": {"code": status, "status": estado_error, "details": [detalle]}},
        headers=headers,
    )


def _crear_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in DDL:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(
            "INSERT INTO usuario (id, estado) VALUES (10, 1), (20, 1)"
        )
        conn.exec_driver_sql("INSERT INTO cita (id) VALUES (1)")
    return engine


@pytest.fixture
def db():
    engine = _crear_engine()
    with Session(engine, autoflush=False) as sesion:
        yield sesion
    engine.dispose()


# --------------------------------------------------------------------------- #
# Constructores de datos
# --------------------------------------------------------------------------- #
def _dispositivo(db: Session, *, usuario_id: int = 10, token: str = TOKEN_A,
                 activo: bool = True) -> DispositivoPush:
    dispositivo = DispositivoPush(
        usuario_id=usuario_id,
        token_fcm=token,
        plataforma="ANDROID",
        activo=activo,
        revocado_en=None if activo else AHORA,
    )
    db.add(dispositivo)
    db.flush()
    return dispositivo


def _notificacion(db: Session, *, usuario_id: int = 10,
                  tipo: str = "CITA_CREADA", evento: str = "evt-1",
                  datos: dict | None = None,
                  cancelada_en=None) -> Notificacion:
    notificacion = Notificacion(
        usuario_id=usuario_id,
        tipo=tipo,
        evento_clave=evento,
        titulo="Cita registrada",
        cuerpo="Tu cita fue registrada.",
        datos=datos if datos is not None else {
            "ruta": "/citas",
            "pantalla": "detalle_cita",
            "seccion": "citas",
            "accion": "ver",
            "cita_id": 1,
        },
        cita_id=1,
        cancelada_en=cancelada_en,
    )
    db.add(notificacion)
    db.flush()
    return notificacion


def _entrega(db: Session, *, notificacion_id: int, dispositivo_id: int,
             estado: str = "PENDIENTE", intentos: int = 0,
             proximo_intento=None, bloqueado_hasta=None,
             lease_token=None, codigo=None) -> NotificacionEntrega:
    entrega = NotificacionEntrega(
        notificacion_id=notificacion_id,
        dispositivo_id=dispositivo_id,
        estado=estado,
        intentos=intentos,
        proximo_intento=proximo_intento,
        bloqueado_hasta=bloqueado_hasta,
        lease_token=lease_token,
        codigo_ultimo_error=codigo,
    )
    db.add(entrega)
    db.flush()
    return entrega


def _preferencia(db: Session, usuario_id: int = 10, **cambios):
    preferencia = PreferenciaNotificacion(usuario_id=usuario_id, **cambios)
    db.add(preferencia)
    db.flush()
    return preferencia


def _ejecutar(db: Session, proveedor, *, configuracion=None, ahora=AHORA):
    return procesar_entregas(
        db,
        proveedor=proveedor,
        configuracion=configuracion or ConfiguracionWorker(tamano_lote=20),
        reloj=lambda: ahora,
    )


def _entregas(db: Session) -> list[NotificacionEntrega]:
    db.expire_all()
    return list(db.scalars(
        select(NotificacionEntrega).order_by(NotificacionEntrega.id)
    ))


# --------------------------------------------------------------------------- #
# 1. Fanout
# --------------------------------------------------------------------------- #
def test_fanout_crea_una_entrega_por_dispositivo_activo(db):
    _dispositivo(db, token=TOKEN_A)
    _dispositivo(db, token=TOKEN_B)
    _dispositivo(db, token=TOKEN_AJENO, usuario_id=20)
    _dispositivo(db, token=TOKEN_C, activo=False)
    db.commit()

    notificacion = _notificacion(db)
    creadas = servicio_entregas.encolar_entregas(db, notificacion)
    db.commit()

    assert len(creadas) == 2
    entregas = _entregas(db)
    assert {e.estado for e in entregas} == {"PENDIENTE"}
    dispositivos = {e.dispositivo_id for e in entregas}
    tokens = {
        d.id: d.token_fcm for d in db.scalars(select(DispositivoPush))
    }
    assert {tokens[dispositivo_id] for dispositivo_id in dispositivos} == {
        TOKEN_A, TOKEN_B,
    }


def test_fanout_con_push_desactivado_conserva_la_bandeja(db):
    _dispositivo(db)
    _preferencia(db, push_habilitado=False)
    db.commit()

    notificacion = _notificacion(db)
    creadas = servicio_entregas.encolar_entregas(db, notificacion)
    db.commit()

    assert creadas == []
    assert db.scalar(select(func.count()).select_from(Notificacion)) == 1
    assert db.scalar(select(func.count()).select_from(NotificacionEntrega)) == 0


def test_fanout_respeta_preferencia_por_tipo_de_evento(db):
    _dispositivo(db)
    _preferencia(db, citas_creadas=False)
    db.commit()

    notificacion = _notificacion(db, tipo="CITA_CREADA")
    assert servicio_entregas.encolar_entregas(db, notificacion) == []

    notificacion_confirmada = _notificacion(
        db, tipo="CITA_CONFIRMADA", evento="evt-2",
    )
    creadas = servicio_entregas.encolar_entregas(db, notificacion_confirmada)
    db.commit()

    assert len(creadas) == 1
    assert db.scalar(select(func.count()).select_from(Notificacion)) == 2


def test_fanout_sin_dispositivos_no_crea_entregas(db):
    notificacion = _notificacion(db)
    assert servicio_entregas.encolar_entregas(db, notificacion) == []
    db.commit()
    assert db.scalar(select(func.count()).select_from(NotificacionEntrega)) == 0


def test_fanout_es_idempotente_por_notificacion_y_dispositivo(db):
    _dispositivo(db)
    _dispositivo(db, token=TOKEN_B)
    db.commit()
    notificacion = _notificacion(db)

    servicio_entregas.encolar_entregas(db, notificacion)
    db.commit()
    segunda = servicio_entregas.encolar_entregas(db, notificacion)
    db.commit()

    assert segunda == []
    assert db.scalar(select(func.count()).select_from(NotificacionEntrega)) == 2


def test_fanout_propaga_fallo_inesperado(db, monkeypatch):
    _dispositivo(db)
    db.commit()
    notificacion = _notificacion(db)

    def explotar(*_args, **_kwargs):
        raise RuntimeError("fallo de persistencia simulado")

    monkeypatch.setattr(servicio_entregas.repo_entregas, "crear", explotar)

    with pytest.raises(RuntimeError):
        servicio_entregas.encolar_entregas(db, notificacion)


def test_rollback_descarta_notificacion_y_entregas(db):
    _dispositivo(db)
    db.commit()

    notificacion = _notificacion(db)
    servicio_entregas.encolar_entregas(db, notificacion)
    assert db.scalar(select(func.count()).select_from(NotificacionEntrega)) == 1

    db.rollback()

    assert db.scalar(select(func.count()).select_from(Notificacion)) == 0
    assert db.scalar(select(func.count()).select_from(NotificacionEntrega)) == 0


def test_evento_de_cita_encola_entregas_en_la_misma_transaccion(db, monkeypatch):
    _dispositivo(db)
    db.commit()
    cita = SimpleNamespace(
        id=1, estado="PROGRAMADA", fecha_registro=AHORA,
        fecha_actualizacion=AHORA,
    )
    usuario = SimpleNamespace(id=10)
    # El destinatario real lo deriva 3C.4 de Cita -> Paciente -> Usuario; aqui
    # se aisla esa resolucion para probar solo la integracion con el outbox.
    monkeypatch.setattr(
        servicio_eventos, "_resolver_usuario_destinatario",
        lambda _db, _cita: 10,
    )

    servicio_eventos.registrar_evento_cita_creada(db, cita, usuario)
    db.commit()

    assert db.scalar(select(func.count()).select_from(CitaEvento)) == 1
    assert db.scalar(select(func.count()).select_from(Notificacion)) == 1
    assert db.scalar(select(func.count()).select_from(NotificacionEntrega)) == 1


# --------------------------------------------------------------------------- #
# 2. Worker: exito, preferencias y dispositivos
# --------------------------------------------------------------------------- #
def test_worker_registra_aceptacion_y_provider_message_id(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(
        db, notificacion_id=notificacion.id, dispositivo_id=1,
    )
    db.commit()
    cliente = ClienteFalso(_aceptado("projects/demo/messages/123"))

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.aceptadas == 1
    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "ACEPTADO_POR_FCM"
    assert actualizada.proveedor_mensaje_id == "projects/demo/messages/123"
    assert actualizada.intentos == 1
    assert actualizada.fecha_aceptado is not None
    assert actualizada.lease_token is None
    assert actualizada.bloqueado_hasta is None
    assert len(cliente.llamadas) == 1


def test_worker_no_marca_aceptado_sin_respuesta_valida(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()
    cliente = ClienteFalso(RespuestaFalsa(200, {}))

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.aceptadas == 0
    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "PENDIENTE"
    assert actualizada.proveedor_mensaje_id is None
    assert actualizada.codigo_ultimo_error == "RESPUESTA_SIN_ID"


def test_worker_revalida_preferencia_despues_del_encolado(db):
    _dispositivo(db)
    db.commit()
    notificacion = _notificacion(db)
    servicio_entregas.encolar_entregas(db, notificacion)
    _preferencia(db, push_habilitado=False)
    db.commit()
    cliente = ClienteFalso(_aceptado())

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.omitidas == 1
    assert cliente.llamadas == []
    entrega = _entregas(db)[0]
    assert entrega.estado == "OMITIDO"
    assert entrega.codigo_ultimo_error == "PREFERENCIA_PUSH_DESHABILITADA"
    # La bandeja in-app sigue disponible.
    assert db.scalar(select(func.count()).select_from(Notificacion)) == 1


def test_worker_omite_dispositivo_revocado_despues_del_encolado(db):
    dispositivo = _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id)
    dispositivo.activo = False
    dispositivo.revocado_en = AHORA
    db.commit()
    cliente = ClienteFalso(_aceptado())

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.omitidas == 1
    assert cliente.llamadas == []
    entrega = _entregas(db)[0]
    assert entrega.estado == "OMITIDO"
    assert entrega.codigo_ultimo_error == "DISPOSITIVO_INACTIVO"


def test_worker_omite_notificacion_cancelada(db):
    _dispositivo(db)
    notificacion = _notificacion(db, cancelada_en=AHORA)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()
    cliente = ClienteFalso(_aceptado())

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.omitidas == 1
    assert _entregas(db)[0].codigo_ultimo_error == "NOTIFICACION_CANCELADA"


def test_worker_no_envia_a_dispositivo_de_otro_usuario(db):
    _dispositivo(db, usuario_id=20, token=TOKEN_AJENO)
    notificacion = _notificacion(db, usuario_id=10)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()
    cliente = ClienteFalso(_aceptado())

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.omitidas == 1
    assert cliente.llamadas == []
    assert _entregas(db)[0].codigo_ultimo_error == "DISPOSITIVO_NO_ELEGIBLE"


# --------------------------------------------------------------------------- #
# 3. Tokens invalidos y clasificacion de errores
# --------------------------------------------------------------------------- #
def test_worker_invalida_token_no_registrado_y_desactiva_dispositivo(db):
    dispositivo = _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id)
    db.commit()
    cliente = ClienteFalso(_error(404, error_code="UNREGISTERED",
                                  estado_error="NOT_FOUND"))

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.invalidadas == 1
    entrega = _entregas(db)[0]
    assert entrega.estado == "INVALIDO"
    assert entrega.codigo_ultimo_error == "UNREGISTERED"
    assert entrega.proximo_intento is None
    db.refresh(dispositivo)
    assert dispositivo.activo is False
    assert dispositivo.revocado_en is not None


def test_worker_no_invalida_token_ante_400_generico(db):
    dispositivo = _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id)
    db.commit()
    cliente = ClienteFalso(_error(400, error_code="INVALID_ARGUMENT"))

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.fallidas == 1
    entrega = _entregas(db)[0]
    assert entrega.estado == "FALLIDO"
    assert entrega.codigo_ultimo_error == "PAYLOAD_INVALIDO"
    db.refresh(dispositivo)
    assert dispositivo.activo is True


def test_worker_respeta_retry_after_en_429(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()
    cliente = ClienteFalso(_error(
        429, estado_error="RESOURCE_EXHAUSTED", headers={"Retry-After": "120"},
    ))

    resultado = _ejecutar(
        db, _proveedor(cliente),
        configuracion=ConfiguracionWorker(
            tamano_lote=5, backoff_base_segundos=30,
        ),
    )

    assert resultado.reprogramadas == 1
    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "PENDIENTE"
    assert actualizada.intentos == 1
    assert actualizada.codigo_ultimo_error == "PROVEEDOR_RATE_LIMIT"
    assert actualizada.proximo_intento == AHORA + timedelta(seconds=120)
    assert actualizada.lease_token is None


def test_worker_aplica_backoff_exponencial_en_5xx(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1,
                       intentos=2)
    db.commit()
    cliente = ClienteFalso(_error(503, estado_error="UNAVAILABLE"))

    resultado = _ejecutar(
        db, _proveedor(cliente),
        configuracion=ConfiguracionWorker(
            tamano_lote=5, max_intentos=5, backoff_base_segundos=30,
            backoff_maximo_segundos=600,
        ),
    )

    assert resultado.reprogramadas == 1
    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "PENDIENTE"
    assert actualizada.intentos == 3
    # 30 * 2^(3-1) = 120
    assert actualizada.proximo_intento == AHORA + timedelta(seconds=120)


def test_worker_timeout_es_reintentable(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()
    cliente = ClienteFalso(httpx.ReadTimeout("timeout simulado"))

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.reprogramadas == 1
    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "PENDIENTE"
    assert actualizada.codigo_ultimo_error == "PROVEEDOR_TIMEOUT"


def test_worker_401_no_invalida_tokens_ni_consume_intentos(db):
    dispositivo = _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id)
    db.commit()
    cliente = ClienteFalso(_error(401, estado_error="UNAUTHENTICATED"))

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.error_configuracion is True
    assert resultado.invalidadas == 0
    entrega = _entregas(db)[0]
    assert entrega.estado == "PENDIENTE"
    assert entrega.intentos == 0
    assert entrega.codigo_ultimo_error == "PROVEEDOR_NO_AUTORIZADO"
    assert entrega.proximo_intento == AHORA + timedelta(seconds=30)
    db.refresh(dispositivo)
    assert dispositivo.activo is True


def test_worker_403_libera_el_resto_del_lote_sin_consumir_intentos(db):
    _dispositivo(db, token=TOKEN_A)
    _dispositivo(db, token=TOKEN_B)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=2)
    db.commit()
    cliente = ClienteFalso(_error(403, estado_error="PERMISSION_DENIED"))

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.error_configuracion is True
    assert len(cliente.llamadas) == 1
    estados = [(e.estado, e.intentos) for e in _entregas(db)]
    assert estados == [("PENDIENTE", 0), ("PENDIENTE", 0)]
    assert resultado.liberadas == 2


def test_worker_payload_invalido_termina_en_fallido(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()
    cliente = ClienteFalso(_error(400, error_code="INVALID_ARGUMENT"))

    _ejecutar(db, _proveedor(cliente))

    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "FALLIDO"
    assert actualizada.proximo_intento is None


def test_worker_agota_maximo_de_intentos(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1,
                       intentos=2)
    db.commit()
    cliente = ClienteFalso(_error(500, estado_error="INTERNAL"))

    resultado = _ejecutar(
        db, _proveedor(cliente),
        configuracion=ConfiguracionWorker(tamano_lote=5, max_intentos=3),
    )

    assert resultado.fallidas == 1
    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "FALLIDO"
    assert actualizada.codigo_ultimo_error == "MAXIMO_INTENTOS_ALCANZADO"
    assert actualizada.intentos == 3
    assert actualizada.proximo_intento is None


def test_worker_sanitiza_codigos_del_proveedor(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()
    respuesta = RespuestaFalsa(
        400,
        {"error": {
            "status": "<script>alert(1)</script>",
            "details": [{"errorCode": "token\ninvalido/../secreto"}],
        }},
    )
    _ejecutar(db, _proveedor(ClienteFalso(respuesta)))

    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "FALLIDO"
    assert actualizada.codigo_ultimo_error == "PAYLOAD_INVALIDO"
    assert re.fullmatch(r"[A-Z0-9_]{1,100}", actualizada.codigo_ultimo_error)


# --------------------------------------------------------------------------- #
# 4. Claim, leases y lotes
# --------------------------------------------------------------------------- #
def test_worker_respeta_limite_de_lote(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    # La unicidad impide 5 entregas de la misma notificacion/dispositivo:
    # se usan 5 notificaciones distintas.
    for indice in range(5):
        notificacion_indice = _notificacion(db, evento=f"evt-lote-{indice}")
        _entrega(
            db,
            notificacion_id=notificacion_indice.id,
            dispositivo_id=1,
        )
    db.commit()
    cliente = ClienteFalso(
        _aceptado("projects/demo/messages/1"),
        _aceptado("projects/demo/messages/2"),
    )

    resultado = _ejecutar(
        db, _proveedor(cliente),
        configuracion=ConfiguracionWorker(tamano_lote=2),
    )
    assert resultado.reclamadas == 2
    assert resultado.aceptadas == 2
    assert len(cliente.llamadas) == 2
    estados = [e.estado for e in _entregas(db)]
    assert estados.count("ACEPTADO_POR_FCM") == 2
    assert estados.count("PENDIENTE") == 3


def test_worker_no_reclama_entregas_con_proximo_intento_futuro(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(
        db, notificacion_id=notificacion.id, dispositivo_id=1,
        proximo_intento=AHORA + timedelta(minutes=5),
    )
    db.commit()
    cliente = ClienteFalso(_aceptado())

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.reclamadas == 0
    assert cliente.llamadas == []
    assert db.get(NotificacionEntrega, entrega.id).estado == "PENDIENTE"


def test_worker_no_reclama_un_lease_activo(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(
        db, notificacion_id=notificacion.id, dispositivo_id=1,
        estado="PROCESANDO",
        bloqueado_hasta=AHORA + timedelta(minutes=10),
        lease_token="lease-de-otro-worker",
    )
    db.commit()
    cliente = ClienteFalso(_aceptado())

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.reclamadas == 0
    assert cliente.llamadas == []
    entrega = _entregas(db)[0]
    assert entrega.estado == "PROCESANDO"
    assert entrega.lease_token == "lease-de-otro-worker"


def test_worker_recupera_un_lease_expirado(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(
        db, notificacion_id=notificacion.id, dispositivo_id=1,
        estado="PROCESANDO",
        bloqueado_hasta=AHORA - timedelta(minutes=1),
        lease_token="lease-vencido",
    )
    db.commit()
    cliente = ClienteFalso(_aceptado())

    resultado = _ejecutar(db, _proveedor(cliente))

    assert resultado.aceptadas == 1
    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "ACEPTADO_POR_FCM"
    assert actualizada.lease_token is None


def test_worker_antiguo_no_sobrescribe_un_lease_nuevo(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()

    # Worker A reclama y "cae" (lease vigente).
    lease_a = repo_entregas.reclamar_lote(
        db, ahora=AHORA, lease_token="lease-viejo",
        lease_hasta=AHORA + timedelta(minutes=1), limite=1,
    )
    db.commit()
    assert len(lease_a) == 1

    # El lease de A expira y B lo recupera.
    lease_b = repo_entregas.reclamar_lote(
        db, ahora=AHORA + timedelta(minutes=2), lease_token="lease-nuevo",
        lease_hasta=AHORA + timedelta(minutes=10), limite=1,
    )
    db.commit()
    assert len(lease_b) == 1

    aplicado = repo_entregas.actualizar_resultado(
        db, entrega.id, lease_token="lease-viejo",
        valores={"estado": "ACEPTADO_POR_FCM", "intentos": 1},
    )
    db.commit()

    assert aplicado is False
    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "PROCESANDO"
    assert actualizada.lease_token == "lease-nuevo"


def test_consulta_de_reclamo_usa_skip_locked_solo_en_postgresql():
    from sqlalchemy.dialects import postgresql, sqlite

    consulta_pg = repo_entregas.construir_consulta_reclamo(
        AHORA, 10, dialecto="postgresql",
    )
    sql_pg = str(consulta_pg.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
    ))
    assert "FOR UPDATE SKIP LOCKED" in sql_pg
    assert "procesando" in sql_pg.lower()

    consulta_sqlite = repo_entregas.construir_consulta_reclamo(
        AHORA, 10, dialecto="sqlite",
    )
    sql_sqlite = str(consulta_sqlite.compile(
        dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True},
    ))
    assert "SKIP LOCKED" not in sql_sqlite.upper()
    assert "LIMIT" in sql_sqlite.upper()


def test_worker_sin_configuracion_no_reclama_nada(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()
    proveedor_sin_configurar = fcm.FcmHttpV1()

    resultado = _ejecutar(db, proveedor_sin_configurar)

    assert resultado.proveedor_no_configurado is True
    assert resultado.reclamadas == 0
    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "PENDIENTE"
    assert actualizada.intentos == 0
    assert actualizada.lease_token is None


def test_proveedor_sin_credenciales_es_error_de_configuracion(db):
    _dispositivo(db)
    notificacion = _notificacion(db)
    entrega = _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()

    class CredencialesVacias:
        def access_token(self):
            return ""

    proveedor = fcm.FcmHttpV1(
        proyecto_id="clinica-oftalmologica-demo",
        credenciales=CredencialesVacias(),
        cliente=ClienteFalso(_aceptado()),
    )

    resultado = _ejecutar(db, proveedor)

    assert resultado.error_configuracion is True
    actualizada = db.get(NotificacionEntrega, entrega.id)
    db.refresh(actualizada)
    assert actualizada.estado == "PENDIENTE"
    assert actualizada.intentos == 0
    assert actualizada.codigo_ultimo_error == "PROVEEDOR_SIN_CREDENCIALES"


# --------------------------------------------------------------------------- #
# 5. Aislamiento tenant
# --------------------------------------------------------------------------- #
def test_worker_solo_procesa_la_sesion_tenant_recibida(db):
    """Dos bases con los mismos IDs locales: el worker no cruza datos."""
    otra_engine = _crear_engine()
    with Session(otra_engine, autoflush=False) as otra:
        _dispositivo(otra, token=TOKEN_C)
        notificacion_b = _notificacion(otra, evento="evt-tenant-b")
        _entrega(
            otra, notificacion_id=notificacion_b.id, dispositivo_id=1,
            estado="PENDIENTE",
        )
        otra.commit()

        _dispositivo(db, token=TOKEN_A)
        notificacion_a = _notificacion(db, evento="evt-tenant-a")
        _entrega(
            db, notificacion_id=notificacion_a.id, dispositivo_id=1,
        )
        db.commit()

        cliente = ClienteFalso(_aceptado())
        resultado = _ejecutar(db, _proveedor(cliente))

        assert resultado.aceptadas == 1

        otra.expire_all()
        entrega_b = otra.scalar(select(NotificacionEntrega))
        assert entrega_b.estado == "PENDIENTE"
        assert entrega_b.intentos == 0
        assert len(cliente.llamadas) == 1
        assert cliente.llamadas[0]["json"]["message"]["token"] == TOKEN_A
    otra_engine.dispose()


# --------------------------------------------------------------------------- #
# 6. Privacidad del payload y de los registros
# --------------------------------------------------------------------------- #
def test_payload_fcm_usa_allowlist_y_strings(db):
    _dispositivo(db)
    notificacion = _notificacion(db, datos={
        "ruta": "/citas",
        "pantalla": "detalle_cita",
        "cita_id": 1,
        "diagnostico": "NO DEBE VIAJAR",
        "paciente": "Ana Perez",
    })
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()
    cliente = ClienteFalso(_aceptado())

    _ejecutar(db, _proveedor(cliente))

    llamada = cliente.llamadas[0]
    mensaje = llamada["json"]["message"]
    assert mensaje["token"] == TOKEN_A
    assert mensaje["notification"] == {
        "title": "Cita registrada", "body": "Tu cita fue registrada.",
    }
    datos = mensaje["data"]
    assert set(datos) <= CLAVES_DATOS_PERMITIDAS
    assert all(isinstance(valor, str) for valor in datos.values())
    assert "diagnostico" not in datos
    serializado = str(llamada["json"]).lower()
    assert "no debe viajar" not in serializado
    assert "ana perez" not in serializado
    assert llamada["url"] == (
        "https://fcm.googleapis.com/v1/projects/"
        "clinica-oftalmologica-demo/messages:send"
    )
    assert llamada["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"


def test_worker_no_registra_tokens_ni_credenciales(db, caplog):
    _dispositivo(db)
    notificacion = _notificacion(db)
    _entrega(db, notificacion_id=notificacion.id, dispositivo_id=1)
    db.commit()
    cliente = ClienteFalso(_error(
        400,
        error_code="INVALID_ARGUMENT",
    ))
    caplog.set_level(logging.DEBUG)

    resultado = _ejecutar(db, _proveedor(cliente))
    texto_logs = caplog.text
    representacion = repr(resultado)

    for secreto in (TOKEN_A, ACCESS_TOKEN, "Bearer"):
        assert secreto not in texto_logs
        assert secreto not in representacion
    assert "token_fcm" not in texto_logs
    entrega = _entregas(db)[0]
    assert TOKEN_A not in str(entrega.codigo_ultimo_error)


def test_worker_at_least_once_window_documentada():
    """La ventana de duplicacion se documenta en el modulo del worker."""
    doc = worker_fcm.__doc__ or ""
    assert "at-least-once" in doc
    assert "exactly-once" in doc


# --------------------------------------------------------------------------- #
# 7. Migracion aditiva preparada (no aplicada)
# --------------------------------------------------------------------------- #
def test_migracion_3c5_es_aditiva_y_no_reemplaza_3c2():
    sql = (
        MIGRACIONES / "versions" / "20261122000000_cu3c5_worker_fcm_push.sql"
    ).read_text(encoding="utf-8")

    assert "ALTER TABLE public.notificacion_entrega" in sql
    assert "ADD COLUMN lease_token VARCHAR(64)" in sql
    assert "OMITIDO" in sql
    assert "DROP CONSTRAINT ck_notificacion_entrega_estado" in sql
    assert "CREATE TABLE" not in sql
    assert "DELETE FROM" not in sql
    # Transaccion controlada por el runner.
    assert not re.search(r"(?im)^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", sql)

    sql_3c2 = (
        MIGRACIONES / "versions" / "20261121000000_cu3c2_notificaciones_push.sql"
    ).read_text(encoding="utf-8")
    assert "lease_token" not in sql_3c2
    assert "'OMITIDO'" not in sql_3c2


def test_manifiesto_declara_la_migracion_como_pendiente_de_aplicar():
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
    assert "v7-sprint5-notificaciones-push" in bundle.allowed_source_schema_versions
    assert "v8-sprint5-worker-fcm-push" in bundle.allowed_source_schema_versions
    assert bundle.preflight_sql.startswith("-- Preflight de Fase 3C.5")


def test_configuracion_del_worker_rechaza_limites_incoherentes():
    with pytest.raises(ValueError):
        ConfiguracionWorker(tamano_lote=0)
    with pytest.raises(ValueError):
        ConfiguracionWorker(lease_segundos=1)
    with pytest.raises(ValueError):
        ConfiguracionWorker(max_intentos=0)
    with pytest.raises(ValueError):
        ConfiguracionWorker(
            lease_segundos=10, timeout_proveedor_segundos=30,
        )
