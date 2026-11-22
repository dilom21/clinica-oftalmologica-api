# -*- coding: utf-8 -*-
"""Fase 3C.5.3 - orquestacion segura de workers Push.

Todo es aislado: SQLite en memoria, Control Plane simulado, proveedor FCM falso
y sesiones inyectadas por una fuente de prueba. No se abren conexiones reales,
no se leen secretos, no se aplican migraciones y no se llama Firebase/FCM.

Limite declarado: SQLite **no** valida la concurrencia real de PostgreSQL ni
``FOR UPDATE SKIP LOCKED``. El lease/fencing se prueba a nivel de consulta y de
orquestacion; la concurrencia PostgreSQL sigue pendiente de un entorno aislado
autorizado (ver 3C.5.1).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.notificaciones_push import orquestacion
from app.modules.notificaciones_push.models.models import (
    DispositivoPush,
    DispositivoPushRegistry,
    Notificacion,
    NotificacionEntrega,
    RevocacionPushGlobal,
)
from app.modules.notificaciones_push.orquestacion import (
    ESTADO_CANCELADO,
    ESTADO_COMPLETADO,
    ESTADO_ERROR_CONTROL_PLANE,
    ESTADO_ERROR_INESPERADO,
    ESTADO_ERROR_PROVEEDOR,
    ESTADO_ERROR_SESION,
    ESTADO_PARCIAL,
    ESTADO_PROVEEDOR_NO_CONFIGURADO,
    ESTADO_SIN_TRABAJO,
    ESTADO_TIEMPO_AGOTADO,
    ConfiguracionCiclo,
    SesionesCiclo,
    ejecutar_ciclo_tenant,
)
from app.modules.notificaciones_push.orquestador import main as cli_main
from app.modules.notificaciones_push.providers import fcm
from app.modules.notificaciones_push.repositories import (
    registro_global as repo_registro,
)
from app.modules.notificaciones_push.repositories import (
    revocaciones as repo_revocaciones,
)
from app.modules.notificaciones_push.seguridad import calcular_huella_token
from app.modules.notificaciones_push.services.revocaciones import (
    ConfiguracionRevocaciones,
)
from app.modules.notificaciones_push.services.revocaciones import (
    registrar_solicitud_revocacion,
)
from app.modules.notificaciones_push.services.worker_fcm import (
    ConfiguracionWorker,
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

CONFIG = ConfiguracionCiclo()

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


def _motor_tenant() -> object:
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


def _motor_control() -> object:
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


# --------------------------------------------------------------------------- #
# Composicion de prueba
# --------------------------------------------------------------------------- #
class FuenteFija:
    """Fuente de sesiones de prueba: un tenant ya resuelto, sin enumerar."""

    def __init__(self, db, tenant_id, control=None) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.control = control
        self.veces_abierta = 0

    def abrir(self) -> SesionesCiclo:
        self.veces_abierta += 1
        return SesionesCiclo(
            db=self.db,
            control=self.control,
            tenant_database_id=self.tenant_id,
        )


class FuenteQueFalla:
    def __init__(self) -> None:
        self.veces_abierta = 0

    def abrir(self) -> SesionesCiclo:  # pragma: no cover - siempre falla
        self.veces_abierta += 1
        raise RuntimeError("no se pudo resolver el tenant")


class Mundo:
    """Una base tenant + Control Plane opcional para un test."""

    def __init__(
        self,
        *,
        tenant_id: int = TENANT_A,
        con_control: bool = True,
        control_engine=None,
    ) -> None:
        self.tenant_id = tenant_id
        self.engine = _motor_tenant()
        self.db = Session(self.engine, autoflush=False)
        self.db.info["tenant_context"] = SimpleNamespace(
            tenant_database_id=tenant_id,
        )
        self.control_engine = control_engine or _motor_control()
        self.control = (
            Session(self.control_engine, autoflush=False)
            if con_control
            else None
        )
        self.fuente = FuenteFija(self.db, tenant_id, self.control)

    # Sesion nueva para verificar despues de que el coordinador cerro la suya.
    def sesion(self) -> Session:
        return Session(self.engine, autoflush=False)

    def sesion_control(self) -> Session:
        return Session(self.control_engine, autoflush=False)


@pytest.fixture
def mundo() -> Mundo:
    return Mundo()


# --------------------------------------------------------------------------- #
# Proveedores falsos
# --------------------------------------------------------------------------- #
class ProveedorAceptado:
    configurado = True

    def __init__(self) -> None:
        self.llamadas = 0

    def enviar(self, **_kwargs):
        self.llamadas += 1
        return fcm.ResultadoFcm(
            fcm.ACEPTADO,
            "ACEPTADO",
            mensaje_id=f"projects/demo/messages/{self.llamadas}",
        )


class ProveedorClasificacion:
    configurado = True

    def __init__(self, clasificacion: str, codigo: str) -> None:
        self.clasificacion = clasificacion
        self.codigo = codigo
        self.llamadas = 0

    def enviar(self, **_kwargs):
        self.llamadas += 1
        return fcm.ResultadoFcm(self.clasificacion, self.codigo)


class ProveedorNoConfigurado:
    configurado = False

    def __init__(self) -> None:
        self.llamadas = 0

    def enviar(self, **_kwargs):  # pragma: no cover - no debe invocarse
        self.llamadas += 1
        raise AssertionError("un proveedor no configurado no debe enviar")


class ProveedorQueFalla:
    configurado = True

    def __init__(self) -> None:
        self.llamadas = 0

    def enviar(self, **_kwargs):
        self.llamadas += 1
        raise RuntimeError("caida simulada del proveedor")


# --------------------------------------------------------------------------- #
# Datos
# --------------------------------------------------------------------------- #
def _dispositivo(
    mundo: Mundo,
    *,
    usuario_id: int = USUARIO,
    token: str = TOKEN_A,
    activo: bool = True,
    id_: int = 1,
) -> DispositivoPush:
    dispositivo = DispositivoPush(
        id=id_,
        usuario_id=usuario_id,
        token_fcm=token,
        plataforma="ANDROID",
        activo=activo,
        revocado_en=None if activo else AHORA,
    )
    mundo.db.add(dispositivo)
    mundo.db.flush()
    return dispositivo


def _notificacion(
    mundo: Mundo, *, usuario_id: int = USUARIO, evento: str = "evt-1",
) -> Notificacion:
    notificacion = Notificacion(
        usuario_id=usuario_id,
        tipo="CITA_CREADA",
        evento_clave=evento,
        titulo="Cita registrada",
        cuerpo="Tu cita fue registrada.",
        datos={"ruta": "/citas"},
    )
    mundo.db.add(notificacion)
    mundo.db.flush()
    return notificacion


def _entrega(
    mundo: Mundo, *, notificacion_id: int, dispositivo_id: int,
    estado: str = "PENDIENTE",
) -> NotificacionEntrega:
    entrega = NotificacionEntrega(
        notificacion_id=notificacion_id,
        dispositivo_id=dispositivo_id,
        estado=estado,
    )
    mundo.db.add(entrega)
    mundo.db.flush()
    return entrega


def _registro(
    mundo: Mundo,
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
    mundo.control.add(registro)
    mundo.control.flush()
    return registro


def _solicitud_pendiente(
    mundo: Mundo, dispositivo: DispositivoPush,
) -> RevocacionPushGlobal:
    solicitud, _ = registrar_solicitud_revocacion(mundo.db, dispositivo, AHORA)
    return solicitud


def _entrega_pendiente(mundo: Mundo) -> NotificacionEntrega:
    dispositivo = _dispositivo(mundo)
    notificacion = _notificacion(mundo)
    entrega = _entrega(
        mundo, notificacion_id=notificacion.id, dispositivo_id=dispositivo.id,
    )
    mundo.db.commit()
    return entrega


def _ejecutar(
    mundo: Mundo,
    proveedor=None,
    *,
    configuracion: ConfiguracionCiclo = CONFIG,
    ahora: datetime = AHORA,
    monotonic=None,
    cancelado=None,
):
    if monotonic is None:
        monotonic = lambda: 0.0  # noqa: E731
    return ejecutar_ciclo_tenant(
        mundo.fuente,
        proveedor=proveedor if proveedor is not None else ProveedorAceptado(),
        configuracion=configuracion,
        reloj=lambda: ahora,
        reloj_monotonico=monotonic,
        cancelado=cancelado,
    )


def _recargar(mundo: Mundo):
    with mundo.sesion() as db:
        entregas = list(db.scalars(
            select(NotificacionEntrega).order_by(NotificacionEntrega.id)
        ))
        revocaciones = list(db.scalars(
            select(RevocacionPushGlobal).order_by(RevocacionPushGlobal.id)
        ))
        return entregas, revocaciones


def _registro_por_token(mundo: Mundo, token: str) -> DispositivoPushRegistry:
    with mundo.sesion_control() as control:
        return control.scalar(
            select(DispositivoPushRegistry).where(
                DispositivoPushRegistry.token_fingerprint
                == calcular_huella_token(token)
            )
        )


# --------------------------------------------------------------------------- #
# A. Ciclo completo, orden y contadores
# --------------------------------------------------------------------------- #
def test_ciclo_completo_procesa_entregas_y_revocaciones(mundo):
    entrega = _entrega_pendiente(mundo)
    entrega_id = entrega.id
    dispositivo = mundo.db.get(DispositivoPush, 1)
    _solicitud_pendiente(mundo, dispositivo)
    registro = _registro(mundo, token=TOKEN_A)
    registro_id = registro.id
    mundo.db.commit()
    mundo.control.commit()

    proveedor = ProveedorAceptado()
    resultado = _ejecutar(mundo, proveedor)

    assert resultado.estado == ESTADO_COMPLETADO
    assert resultado.codigo_salida == 0
    assert resultado.entregas.aceptadas == 1
    assert resultado.entregas.reclamadas == 1
    assert resultado.revocaciones.completadas == 1
    assert resultado.sesiones_abiertas is True
    assert resultado.sesiones_cerradas is True
    assert mundo.fuente.veces_abierta == 1

    entregas, revocaciones = _recargar(mundo)
    assert entregas[0].id == entrega_id
    assert entregas[0].estado == "ACEPTADO_POR_FCM"
    assert entregas[0].proveedor_mensaje_id == "projects/demo/messages/1"
    assert entregas[0].lease_token is None
    assert revocaciones[0].estado == "COMPLETADA"

    with mundo.sesion_control() as control:
        actual = control.get(DispositivoPushRegistry, registro_id)
        assert actual.estado == "REVOCADO"


def test_ciclo_procesa_revocacion_generada_en_la_misma_ejecucion(mundo):
    _entrega_pendiente(mundo)
    _registro(mundo, token=TOKEN_A)
    mundo.control.commit()

    proveedor = ProveedorClasificacion(
        fcm.TOKEN_NO_REGISTRADO, "UNREGISTERED",
    )
    resultado = _ejecutar(mundo, proveedor)

    assert resultado.entregas.invalidadas == 1
    assert resultado.entregas.revocaciones_solicitadas == 1
    assert resultado.revocaciones.completadas == 1
    assert resultado.estado == ESTADO_COMPLETADO

    _, revocaciones = _recargar(mundo)
    assert len(revocaciones) == 1
    assert revocaciones[0].estado == "COMPLETADA"
    assert _registro_por_token(mundo, TOKEN_A).estado == "REVOCADO"


def test_proveedor_no_configurado_no_reclama_ni_consume_intentos(mundo):
    entrega = _entrega_pendiente(mundo)
    entrega_id = entrega.id
    dispositivo = mundo.db.get(DispositivoPush, 1)
    solicitud = _solicitud_pendiente(mundo, dispositivo)
    solicitud_id = solicitud.id
    _registro(mundo, token=TOKEN_A)
    mundo.db.commit()
    mundo.control.commit()

    proveedor = ProveedorNoConfigurado()
    resultado = _ejecutar(mundo, proveedor)

    assert resultado.estado == ESTADO_PROVEEDOR_NO_CONFIGURADO
    assert resultado.codigo_salida == 4
    assert resultado.entregas.reclamadas == 0
    assert resultado.entregas.proveedor_no_configurado is True
    assert proveedor.llamadas == 0
    # Las revocaciones ya pendientes si pueden procesarse: la sesion Control
    # Plane fue autorizada y esta disponible.
    assert resultado.revocaciones.completadas == 1

    entregas, revocaciones = _recargar(mundo)
    assert entregas[0].id == entrega_id
    assert entregas[0].estado == "PENDIENTE"
    assert entregas[0].intentos == 0
    assert entregas[0].lease_token is None
    assert revocaciones[0].id == solicitud_id
    assert revocaciones[0].estado == "COMPLETADA"


def test_error_de_configuracion_del_proveedor_no_es_exito(mundo):
    _entrega_pendiente(mundo)
    proveedor = ProveedorClasificacion(
        fcm.ERROR_CONFIGURACION, "PROVEEDOR_NO_AUTORIZADO",
    )

    resultado = _ejecutar(mundo, proveedor)

    assert resultado.estado == ESTADO_ERROR_PROVEEDOR
    assert resultado.codigo_salida == 2
    assert resultado.entregas.error_configuracion is True
    assert resultado.entregas.liberadas == 1

    entregas, _ = _recargar(mundo)
    assert entregas[0].estado == "PENDIENTE"
    assert entregas[0].intentos == 0
    assert entregas[0].lease_token is None


def test_lote_vacio_termina_sin_trabajo(mundo):
    resultado = _ejecutar(mundo, ProveedorAceptado())

    assert resultado.estado == ESTADO_SIN_TRABAJO
    assert resultado.codigo_salida == 0
    assert resultado.entregas.reclamadas == 0
    assert resultado.revocaciones.reclamadas == 0
    assert resultado.sesiones_cerradas is True


def test_reintento_de_entrega_se_reprograma_sin_marcar_fallo(mundo):
    _entrega_pendiente(mundo)
    proveedor = ProveedorClasificacion(
        fcm.REINTENTABLE, "PROVEEDOR_NO_DISPONIBLE",
    )

    resultado = _ejecutar(mundo, proveedor)

    assert resultado.estado == ESTADO_COMPLETADO
    assert resultado.entregas.reprogramadas == 1
    assert resultado.entregas.fallidas == 0

    entregas, _ = _recargar(mundo)
    assert entregas[0].estado == "PENDIENTE"
    assert entregas[0].intentos == 1
    assert entregas[0].proximo_intento is not None
    assert entregas[0].lease_token is None


def test_fallo_permanente_de_entrega_marca_ciclo_parcial(mundo):
    _entrega_pendiente(mundo)
    proveedor = ProveedorClasificacion(fcm.PERMANENTE, "PAYLOAD_INVALIDO")

    resultado = _ejecutar(mundo, proveedor)

    assert resultado.estado == ESTADO_PARCIAL
    assert resultado.codigo_salida == 1
    assert resultado.entregas.fallidas == 1

    entregas, _ = _recargar(mundo)
    assert entregas[0].estado == "FALLIDO"
    assert entregas[0].codigo_ultimo_error == "PAYLOAD_INVALIDO"


def test_limite_de_lote_se_respeta(mundo):
    dispositivo = _dispositivo(mundo)
    for indice in range(3):
        notificacion = _notificacion(mundo, evento=f"evt-{indice}")
        _entrega(
            mundo,
            notificacion_id=notificacion.id,
            dispositivo_id=dispositivo.id,
        )
    mundo.db.commit()

    configuracion = ConfiguracionCiclo(
        entregas=ConfiguracionWorker(tamano_lote=1),
        revocaciones=ConfiguracionRevocaciones(tamano_lote=1),
    )
    resultado = _ejecutar(mundo, ProveedorAceptado(), configuracion=configuracion)

    assert resultado.entregas.reclamadas == 1
    assert resultado.entregas.aceptadas == 1

    entregas, _ = _recargar(mundo)
    estados = sorted(entrega.estado for entrega in entregas)
    assert estados == ["ACEPTADO_POR_FCM", "PENDIENTE", "PENDIENTE"]


# --------------------------------------------------------------------------- #
# B. Sesiones, Control Plane y fallos transaccionales
# --------------------------------------------------------------------------- #
def test_fallo_al_abrir_sesion_tenant_no_es_exito():
    fuente = FuenteQueFalla()
    proveedor = ProveedorAceptado()

    resultado = ejecutar_ciclo_tenant(
        fuente,
        proveedor=proveedor,
        configuracion=CONFIG,
        reloj=lambda: AHORA,
        reloj_monotonico=lambda: 0.0,
    )

    assert resultado.estado == ESTADO_ERROR_SESION
    assert resultado.codigo_salida == 2
    assert resultado.error_codigo == "SESION_TENANT_NO_DISPONIBLE"
    assert resultado.sesiones_abiertas is False
    assert resultado.entregas is None
    assert resultado.revocaciones is None
    assert proveedor.llamadas == 0
    assert fuente.veces_abierta == 1


def test_sin_control_plane_no_finge_revocaciones(mundo):
    mundo.fuente.control = None
    _entrega_pendiente(mundo)
    dispositivo = mundo.db.get(DispositivoPush, 1)
    solicitud = _solicitud_pendiente(mundo, dispositivo)
    solicitud_id = solicitud.id
    mundo.db.commit()

    resultado = _ejecutar(mundo, ProveedorAceptado())

    assert resultado.estado == ESTADO_ERROR_CONTROL_PLANE
    assert resultado.codigo_salida == 2
    assert resultado.control_plane_disponible is False
    assert resultado.revocaciones is None
    # El worker de entregas no depende del Control Plane y si proceso el lote.
    assert resultado.entregas.aceptadas == 1

    entregas, revocaciones = _recargar(mundo)
    assert entregas[0].estado == "ACEPTADO_POR_FCM"
    assert revocaciones[0].id == solicitud_id
    assert revocaciones[0].estado == "PENDIENTE"
    assert resultado.sesiones_cerradas is True


def test_contexto_tenant_incorrecto_rechaza_el_ciclo(mundo):
    mundo.db.info["tenant_context"] = SimpleNamespace(
        tenant_database_id=TENANT_B,
    )
    _entrega_pendiente(mundo)
    proveedor = ProveedorAceptado()

    resultado = _ejecutar(mundo, proveedor)

    assert resultado.estado == ESTADO_ERROR_SESION
    assert resultado.error_codigo == "SESION_TENANT_NO_DISPONIBLE"
    assert proveedor.llamadas == 0
    assert resultado.sesiones_cerradas is True


def test_fallo_antes_del_commit_global_no_es_exito(mundo, monkeypatch):
    dispositivo = _dispositivo(mundo)
    _solicitud_pendiente(mundo, dispositivo)
    registro = _registro(mundo, token=TOKEN_A)
    registro_id = registro.id
    mundo.db.commit()
    mundo.control.commit()

    def _caida(*_args, **_kwargs):
        raise SQLAlchemyError("fallo simulado del Control Plane")

    monkeypatch.setattr(repo_registro, "revocar_huella_acotada", _caida)
    resultado = _ejecutar(mundo, ProveedorAceptado())

    assert resultado.estado == ESTADO_PARCIAL
    assert resultado.codigo_salida == 1
    assert resultado.revocaciones.reprogramadas == 1
    assert resultado.revocaciones.completadas == 0

    _, revocaciones = _recargar(mundo)
    assert revocaciones[0].estado == "PENDIENTE"
    with mundo.sesion_control() as control:
        assert control.get(DispositivoPushRegistry, registro_id).estado == "ACTIVO"


def test_fallo_despues_del_commit_global_antes_del_ack_es_recuperable(
    mundo, monkeypatch,
):
    dispositivo = _dispositivo(mundo)
    _solicitud_pendiente(mundo, dispositivo)
    registro = _registro(mundo, token=TOKEN_A)
    registro_id = registro.id
    mundo.db.commit()
    mundo.control.commit()

    original = repo_revocaciones.actualizar_resultado

    def _falla_una_vez(*args, **kwargs):
        raise RuntimeError("caida simulada durante el ACK tenant")

    monkeypatch.setattr(repo_revocaciones, "actualizar_resultado", _falla_una_vez)
    primero = _ejecutar(mundo, ProveedorAceptado())

    assert primero.estado == ESTADO_ERROR_INESPERADO
    assert primero.codigo_salida == 3
    assert primero.sesiones_cerradas is True
    # El Control Plane ya quedo confirmado; la solicitud tenant sigue en curso.
    with mundo.sesion_control() as control:
        assert control.get(DispositivoPushRegistry, registro_id).estado == "REVOCADO"
    _, revocaciones = _recargar(mundo)
    assert revocaciones[0].estado == "PROCESANDO"

    # Segunda ejecucion: el lease vencio y el reintento es idempotente.
    monkeypatch.setattr(repo_revocaciones, "actualizar_resultado", original)
    segundo = _ejecutar(
        mundo,
        ProveedorAceptado(),
        ahora=AHORA + LEASE + timedelta(seconds=1),
        monotonic=lambda: 0.0,
    )
    assert segundo.estado == ESTADO_COMPLETADO
    assert segundo.revocaciones.ya_revocadas == 1
    _, revocaciones = _recargar(mundo)
    assert revocaciones[0].estado == "COMPLETADA"


def test_fallo_inesperado_del_proveedor_cierra_sesiones_y_deja_lease(mundo):
    entrega = _entrega_pendiente(mundo)
    entrega_id = entrega.id
    proveedor = ProveedorQueFalla()

    resultado = _ejecutar(mundo, proveedor)

    assert resultado.estado == ESTADO_ERROR_INESPERADO
    assert resultado.codigo_salida == 3
    assert resultado.sesiones_cerradas is True
    # La etapa fallo de forma inesperada: no se reportan contadores parciales,
    # solo el estado y el lease recuperable.
    assert resultado.entregas is None
    assert proveedor.llamadas == 1

    entregas, _ = _recargar(mundo)
    assert entregas[0].id == entrega_id
    assert entregas[0].estado == "PROCESANDO"
    assert entregas[0].lease_token is not None
    assert entregas[0].bloqueado_hasta is not None

    # Mismo tenant, lease vencido: el reintento recupera la entrega.
    segundo = _ejecutar(
        mundo,
        ProveedorAceptado(),
        ahora=AHORA + LEASE + timedelta(seconds=1),
    )
    assert segundo.estado == ESTADO_COMPLETADO
    assert segundo.entregas.aceptadas == 1
    entregas, _ = _recargar(mundo)
    assert entregas[0].estado == "ACEPTADO_POR_FCM"


# --------------------------------------------------------------------------- #
# C. Cancelacion, presupuesto y limites
# --------------------------------------------------------------------------- #
def test_cancelacion_antes_de_iniciar_no_reclama(mundo):
    _entrega_pendiente(mundo)

    resultado = _ejecutar(
        mundo, ProveedorAceptado(), cancelado=lambda: True,
    )

    assert resultado.estado == ESTADO_CANCELADO
    assert resultado.codigo_salida == 3
    assert resultado.entregas is None
    assert resultado.sesiones_cerradas is True
    entregas, _ = _recargar(mundo)
    assert entregas[0].estado == "PENDIENTE"
    assert entregas[0].intentos == 0


def test_cancelacion_tras_entregas_omite_revocaciones(mundo):
    _entrega_pendiente(mundo)
    dispositivo = mundo.db.get(DispositivoPush, 1)
    _solicitud_pendiente(mundo, dispositivo)
    mundo.db.commit()

    llamadas = iter([False, True])
    resultado = _ejecutar(
        mundo, ProveedorAceptado(), cancelado=lambda: next(llamadas),
    )

    assert resultado.estado == ESTADO_CANCELADO
    assert resultado.codigo_salida == 3
    assert resultado.entregas.aceptadas == 1
    assert resultado.revocaciones is None

    _, revocaciones = _recargar(mundo)
    assert revocaciones[0].estado == "PENDIENTE"


def test_presupuesto_de_tiempo_agotado_omite_revocaciones(mundo):
    _entrega_pendiente(mundo)
    dispositivo = mundo.db.get(DispositivoPush, 1)
    _solicitud_pendiente(mundo, dispositivo)
    mundo.db.commit()

    tiempos = iter([0.0, 500.0, 500.0])
    resultado = _ejecutar(
        mundo, ProveedorAceptado(), monotonic=lambda: next(tiempos),
    )

    assert resultado.estado == ESTADO_TIEMPO_AGOTADO
    assert resultado.codigo_salida == 3
    assert resultado.revocaciones is None
    _, revocaciones = _recargar(mundo)
    assert revocaciones[0].estado == "PENDIENTE"


@pytest.mark.parametrize(
    "configuracion",
    [
        lambda: ConfiguracionCiclo(
            entregas=ConfiguracionWorker(tamano_lote=0),
        ),
        lambda: ConfiguracionCiclo(
            revocaciones=ConfiguracionRevocaciones(tamano_lote=101),
        ),
        lambda: ConfiguracionCiclo(
            entregas=ConfiguracionWorker(lease_segundos=60),
            revocaciones=ConfiguracionRevocaciones(lease_segundos=60),
            duracion_maxima_segundos=120,
        ),
        lambda: ConfiguracionCiclo(duracion_maxima_segundos=0),
        lambda: ConfiguracionCiclo(duracion_maxima_segundos=1000),
    ],
    ids=["lote-0", "lote-101", "duracion-mayor-lease", "duracion-0", "duracion-alta"],
)
def test_limites_invalidos_se_rechazan(configuracion):
    with pytest.raises(ValueError):
        configuracion()


def test_cli_rechaza_configuracion_invalida_sin_abrir_sesiones(capsys):
    assert cli_main(
        ["--empresa-codigo", "empresa_prueba", "--lote-entregas", "0"]
    ) == 2
    salida = json.loads(capsys.readouterr().err)
    assert salida["estado"] == "CONFIGURACION_INVALIDA"


def test_cli_rechaza_codigo_de_empresa_invalido(capsys):
    assert cli_main(["--empresa-codigo", "../otra-base"]) == 2
    salida = json.loads(capsys.readouterr().err)
    assert salida["codigo_salida"] == 2


# --------------------------------------------------------------------------- #
# D. Lease, fencing y ejecuciones simultaneas
# --------------------------------------------------------------------------- #
def test_lease_vigente_impide_el_segundo_ciclo_y_vencido_lo_recupera(mundo):
    dispositivo = _dispositivo(mundo)
    solicitud = _solicitud_pendiente(mundo, dispositivo)
    solicitud_id = solicitud.id
    _registro(mundo, token=TOKEN_A)
    mundo.db.commit()
    mundo.control.commit()

    with mundo.sesion() as otro_worker:
        reclamadas = repo_revocaciones.reclamar_lote(
            otro_worker,
            ahora=AHORA,
            lease_token="lease-otro-worker",
            lease_hasta=AHORA + LEASE,
            limite=10,
        )
        ids_reclamados = [item.id for item in reclamadas]
        otro_worker.commit()
    assert ids_reclamados == [solicitud_id]

    resultado = _ejecutar(mundo, ProveedorAceptado())
    assert resultado.revocaciones.reclamadas == 0
    assert resultado.estado == ESTADO_SIN_TRABAJO

    _, revocaciones = _recargar(mundo)
    assert revocaciones[0].lease_token == "lease-otro-worker"
    assert revocaciones[0].estado == "PROCESANDO"

    # El worker antiguo ya no puede escribir con su lease.
    with mundo.sesion() as viejo:
        aplicado = repo_revocaciones.actualizar_resultado(
            viejo,
            solicitud_id,
            lease_token="lease-vencido-distinto",
            valores={"estado": "COMPLETADA", "lease_token": None},
        )
        viejo.commit()
    assert aplicado is False

    segundo = _ejecutar(
        mundo,
        ProveedorAceptado(),
        ahora=AHORA + LEASE + timedelta(seconds=1),
    )
    assert segundo.revocaciones.completadas == 1
    _, revocaciones = _recargar(mundo)
    assert revocaciones[0].estado == "COMPLETADA"


# --------------------------------------------------------------------------- #
# E. Aislamiento multitenant
# --------------------------------------------------------------------------- #
def test_aislamiento_con_dos_tenants_e_ids_locales_iguales():
    control_engine = _motor_control()
    mundo_a = Mundo(tenant_id=TENANT_A, control_engine=control_engine)
    mundo_b = Mundo(tenant_id=TENANT_B, control_engine=control_engine)

    # Mismos IDs locales (dispositivo 1, usuario 9001), tokens distintos.
    for mundo, token in ((mundo_a, TOKEN_A), (mundo_b, TOKEN_B)):
        dispositivo = _dispositivo(mundo, token=token, id_=1)
        notificacion = _notificacion(mundo)
        _entrega(
            mundo,
            notificacion_id=notificacion.id,
            dispositivo_id=dispositivo.id,
        )
        _solicitud_pendiente(mundo, dispositivo)
        _registro(mundo, token=token, tenant_id=mundo.tenant_id)
        mundo.db.commit()
        mundo.control.commit()

    resultado_a = _ejecutar(mundo_a, ProveedorAceptado())
    assert resultado_a.revocaciones.completadas == 1
    assert _registro_por_token(mundo_a, TOKEN_A).estado == "REVOCADO"
    assert _registro_por_token(mundo_a, TOKEN_B).estado == "ACTIVO"

    resultado_b = _ejecutar(mundo_b, ProveedorAceptado())
    assert resultado_b.revocaciones.completadas == 1
    assert _registro_por_token(mundo_b, TOKEN_A).estado == "REVOCADO"
    assert _registro_por_token(mundo_b, TOKEN_B).estado == "REVOCADO"

    # Ningun dispositivo/entrega de un tenant fue tocado por el otro.
    assert _recargar(mundo_a)[0][0].estado == "ACEPTADO_POR_FCM"
    assert _recargar(mundo_b)[0][0].estado == "ACEPTADO_POR_FCM"


# --------------------------------------------------------------------------- #
# F. Privacidad, ausencia de endpoints y punto de entrada
# --------------------------------------------------------------------------- #
def test_logs_agregados_y_sanitizados(mundo, caplog):
    caplog.set_level(logging.DEBUG)
    _entrega_pendiente(mundo)

    resultado = _ejecutar(
        mundo,
        ProveedorClasificacion(fcm.TOKEN_NO_REGISTRADO, "UNREGISTERED"),
    )

    huella = calcular_huella_token(TOKEN_A)
    registros = [
        registro for registro in caplog.records
        if getattr(registro, "ciclo_push", None) is not None
    ]
    assert registros
    resumen = registros[-1].ciclo_push
    assert resumen["ejecucion_id"] == resultado.ejecucion_id
    texto = json.dumps(resumen)
    assert TOKEN_A not in texto
    assert huella not in texto
    assert TOKEN_A not in caplog.text
    assert huella not in caplog.text
    assert "Cita registrada" not in texto


def test_resumen_no_expone_datos_sensibles(mundo):
    _entrega_pendiente(mundo)
    resultado = _ejecutar(mundo, ProveedorAceptado())

    resumen = json.dumps(resultado.resumen())
    assert len(resultado.ejecucion_id) == 16
    assert TOKEN_A not in resumen
    assert calcular_huella_token(TOKEN_A) not in resumen
    assert "tenant_database_id" not in resumen
    assert set(resumen)  # dict serializable, sin objetos ORM


def test_no_existe_ruta_http_para_ejecutar_workers():
    from app.main import app

    rutas = " ".join(app.openapi()["paths"]).lower()
    assert "worker" not in rutas
    assert "orquestador" not in rutas
    assert "ciclo" not in rutas
    assert "/notificaciones-push/ejecutar" not in rutas


def test_app_main_no_inicia_ni_importa_el_orquestador():
    fuente = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "orquestador" not in fuente
    assert "orquestacion" not in fuente
    assert "procesar_entregas" not in fuente
    assert "procesar_revocaciones" not in fuente


def test_sesiones_cerrar_es_idempotente():
    sesiones = SesionesCiclo(db=None, tenant_database_id=TENANT_A)
    sesiones.cerrar()
    sesiones.cerrar()
    assert sesiones.cerrada is True
