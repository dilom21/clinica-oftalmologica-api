# -*- coding: utf-8 -*-
"""Fase 3C.4 - eventos de citas y notificaciones in-app.

Todas las pruebas usan SQLite en memoria y dependencias sustituidas. No se
conecta a bases reales, no se aplican migraciones y no se llama FCM/APNs.

Cubre la integracion de ``registrar_cita``, ``reprogramar_cita`` y
``cambiar_estado_cita`` con ``cita_evento`` y ``notificacion`` dentro de la
misma transaccion tenant.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.gestion_agenda_citas.schemas.schemas import (
    CitaCreate,
    CitaUpdate,
)
from app.modules.gestion_agenda_citas.services import citas
from app.modules.notificaciones_push.models.models import (
    CitaEvento,
    Notificacion,
)
from app.modules.notificaciones_push.repositories import (
    eventos_cita as repo_eventos,
)
from app.modules.notificaciones_push.repositories import (
    notificaciones as repo_notificaciones,
)
from app.modules.notificaciones_push.services import eventos_cita as servicio

AHORA = datetime.now(timezone.utc).replace(tzinfo=None)
DIA = date.today() + timedelta(days=10)
DIA2 = DIA + timedelta(days=1)
DIA3 = DIA + timedelta(days=2)
CLAVES_DATOS_PERMITIDAS = {"ruta", "pantalla", "seccion", "accion", "cita_id"}

DDL = [
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
    """CREATE TABLE paciente (
        id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
        nombres TEXT NOT NULL, apellidos TEXT NOT NULL, ci TEXT,
        fecha_nacimiento DATE, sexo TEXT, telefono TEXT, contacto_emergencia TEXT,
        fecha_registro TIMESTAMP NOT NULL, direccion TEXT, estado BOOLEAN NOT NULL
    )""",
    """CREATE TABLE oftalmologo (
        id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL REFERENCES usuario(id),
        matricula TEXT NOT NULL, nombres TEXT NOT NULL, apellidos TEXT NOT NULL,
        especialidad TEXT, estado BOOLEAN NOT NULL, fecha_registro TIMESTAMP NOT NULL
    )""",
    """CREATE TABLE cita (
        id INTEGER PRIMARY KEY, paciente_id INTEGER NOT NULL REFERENCES paciente(id),
        oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
        fecha DATE NOT NULL, hora_inicio TIME NOT NULL, hora_fin TIME NOT NULL,
        motivo TEXT, observaciones TEXT, estado TEXT NOT NULL, canal TEXT,
        creado_por_usuario_id INTEGER REFERENCES usuario(id),
        fecha_registro TIMESTAMP NOT NULL, fecha_actualizacion TIMESTAMP NOT NULL
    )""",
    """CREATE TABLE bitacora (
        id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
        fecha_hora TIMESTAMP NOT NULL, ip TEXT, accion TEXT NOT NULL,
        entidad_afectada TEXT, id_registro_afectado INTEGER, descripcion TEXT
    )""",
    """CREATE TABLE cita_evento (
        id INTEGER PRIMARY KEY,
        cita_id INTEGER NOT NULL REFERENCES cita(id),
        tipo VARCHAR(30) NOT NULL,
        estado_anterior VARCHAR(20),
        estado_nuevo VARCHAR(20),
        fecha_anterior DATE, hora_anterior TIME,
        fecha_nueva DATE, hora_nueva TIME,
        fecha_evento TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actor_usuario_id INTEGER REFERENCES usuario(id),
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
]


def _crear_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        for ddl in DDL:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(
            "INSERT INTO rol VALUES "
            "(1,'Administrador',NULL,1,1,?),(4,'Paciente',NULL,1,0,?)",
            (AHORA, AHORA),
        )
        conn.exec_driver_sql(
            "INSERT INTO usuario VALUES "
            "(1,'admin@test','h',1,?,1),"
            "(10,'paciente1@test','h',1,?,4),"
            "(11,'paciente2@test','h',0,?,4),"
            "(12,'paciente3@test','h',1,?,4)",
            (AHORA, AHORA, AHORA, AHORA),
        )
        conn.exec_driver_sql(
            "INSERT INTO paciente VALUES "
            "(1,10,'Ana','Perez','CI-001',NULL,NULL,NULL,NULL,?,'Dir',1),"
            "(2,11,'Inactiva','Cuenta','CI-002',NULL,NULL,NULL,NULL,?,'Dir',1),"
            "(3,NULL,'Sin','Usuario','CI-003',NULL,NULL,NULL,NULL,?,'Dir',1),"
            "(4,12,'Otra','Persona','CI-004',NULL,NULL,NULL,NULL,?,'Dir',1)",
            (AHORA, AHORA, AHORA, AHORA),
        )
        conn.exec_driver_sql(
            "INSERT INTO oftalmologo VALUES "
            "(1,1,'MAT-1','Salet','Ejemplo','General',1,?)",
            (AHORA,),
        )
    return engine


@pytest.fixture
def db():
    engine = _crear_engine()
    with Session(engine, autoflush=False) as sesion:
        yield sesion
    engine.dispose()


def _usuario(db: Session, usuario_id: int = 1):
    from app.modules.gestion_usuarios_seguridad.models.models import Usuario

    return db.get(Usuario, usuario_id)


def _cita(db: Session, paciente_id: int = 1) -> Cita:
    from app.modules.gestion_agenda_citas.repositories import repository as repo

    cita = repo.crear_cita(
        db,
        paciente_id=paciente_id,
        oftalmologo_id=1,
        fecha=DIA,
        hora_inicio=time(9, 0),
        hora_fin=time(9, 30),
        motivo="Dolor ocular",
        observaciones="Texto clinico privado",
        estado="PROGRAMADA",
        canal="WEB",
        creado_por_usuario_id=1,
    )
    db.commit()
    return cita


def _disponibilidad():
    return SimpleNamespace(
        tiene_horario=True,
        intervalos_disponibles=[
            SimpleNamespace(hora_inicio=time(8, 0), hora_fin=time(18, 0))
        ],
    )


def _eventos(db: Session) -> list[CitaEvento]:
    return list(db.scalars(select(CitaEvento).order_by(CitaEvento.id)))


def _notificaciones(db: Session) -> list[Notificacion]:
    return list(db.scalars(select(Notificacion).order_by(Notificacion.id)))


def _contar(db: Session, modelo) -> int:
    return int(db.scalar(select(func.count()).select_from(modelo)) or 0)


def _registrar(db: Session, datos=None):
    return citas.registrar_cita(
        db,
        datos
        or CitaCreate(
            paciente_id=1,
            oftalmologo_id=1,
            fecha=DIA,
            hora_inicio=time(10, 0),
            motivo="Dolor ocular",
            observaciones="Texto clinico privado",
        ),
        _usuario(db),
    )


# =========================================================
# Creacion
# =========================================================


def test_creacion_genera_un_evento_y_una_notificacion(db, monkeypatch):
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )
    cita = _registrar(db)

    eventos = _eventos(db)
    assert len(eventos) == 1
    assert eventos[0].tipo == "CITA_CREADA"
    assert eventos[0].cita_id == cita.id
    assert eventos[0].estado_anterior is None
    assert eventos[0].actor_usuario_id == 1

    notificaciones = _notificaciones(db)
    assert len(notificaciones) == 1
    notif = notificaciones[0]
    assert notif.usuario_id == 10
    assert notif.tipo == "CITA_CREADA"
    assert notif.cita_id == cita.id
    assert notif.evento_clave == eventos[0].clave_idempotencia
    assert notif.cuerpo == "Tu cita fue registrada."
    assert set(notif.datos or {}) <= CLAVES_DATOS_PERMITIDAS
    assert (notif.datos or {})["cita_id"] == cita.id


def test_paciente_sin_usuario_conserva_evento_sin_notificacion(db, monkeypatch):
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )
    datos = CitaCreate(
        paciente_id=3,
        oftalmologo_id=1,
        fecha=DIA,
        hora_inicio=time(11, 0),
    )
    _registrar(db, datos)

    assert len(_eventos(db)) == 1
    assert _notificaciones(db) == []


def test_paciente_con_cuenta_inactiva_no_recibe_notificacion(db, monkeypatch):
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )
    datos = CitaCreate(
        paciente_id=2,
        oftalmologo_id=1,
        fecha=DIA,
        hora_inicio=time(12, 0),
    )
    _registrar(db, datos)

    assert len(_eventos(db)) == 1
    assert _notificaciones(db) == []


def test_retry_de_creacion_no_duplica_evento_ni_notificacion(db, monkeypatch):
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )
    cita = _registrar(db)

    servicio.registrar_evento_cita_creada(db, cita, _usuario(db))
    db.commit()

    assert _contar(db, CitaEvento) == 1
    assert _contar(db, Notificacion) == 1


def test_fallo_al_insertar_evento_revierte_la_cita(db, monkeypatch):
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )

    def fallar(*args, **kwargs):
        raise RuntimeError("fallo simulado de persistencia")

    monkeypatch.setattr(repo_eventos, "registrar", fallar)

    with pytest.raises(RuntimeError):
        _registrar(db)

    assert _contar(db, Cita) == 0
    assert _contar(db, CitaEvento) == 0
    assert _contar(db, Notificacion) == 0


def test_fallo_al_insertar_notificacion_revierte_cita_y_evento(db, monkeypatch):
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )

    def fallar(*args, **kwargs):
        raise RuntimeError("fallo simulado de notificacion")

    monkeypatch.setattr(repo_notificaciones, "crear", fallar)

    with pytest.raises(RuntimeError):
        _registrar(db)

    assert _contar(db, Cita) == 0
    assert _contar(db, CitaEvento) == 0
    assert _contar(db, Notificacion) == 0


def test_el_request_no_elige_destinatario(db, monkeypatch):
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )
    # Un campo `usuario_id` inyectado no es parte del contrato de CitaCreate:
    # el destinatario se deriva de Cita -> Paciente -> Usuario.
    datos = CitaCreate(
        paciente_id=1,
        oftalmologo_id=1,
        fecha=DIA,
        hora_inicio=time(10, 0),
        usuario_id=12,
    )
    _registrar(db, datos)

    notificaciones = _notificaciones(db)
    assert [n.usuario_id for n in notificaciones] == [10]


# =========================================================
# Reprogramacion
# =========================================================


def test_reprogramacion_genera_evento_sin_evento_de_estado(db, monkeypatch):
    cita = _cita(db)
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )

    citas.reprogramar_cita(
        db,
        cita.id,
        CitaUpdate(fecha=DIA2, hora_inicio=time(11, 0)),
        _usuario(db),
    )

    eventos = _eventos(db)
    assert [e.tipo for e in eventos] == ["CITA_REPROGRAMADA"]
    evento = eventos[0]
    assert evento.fecha_anterior == DIA
    assert evento.hora_anterior == time(9, 0)
    assert evento.fecha_nueva == DIA2
    assert evento.hora_nueva == time(11, 0)

    notificaciones = _notificaciones(db)
    assert len(notificaciones) == 1
    assert notificaciones[0].tipo == "CITA_REPROGRAMADA"
    assert notificaciones[0].cuerpo == "Tu cita fue reprogramada."


def test_reprogramacion_solo_motivo_no_genera_evento(db, monkeypatch):
    cita = _cita(db)
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )

    citas.reprogramar_cita(
        db,
        cita.id,
        CitaUpdate(motivo="Control postoperatorio"),
        _usuario(db),
    )

    assert _eventos(db) == []
    assert _notificaciones(db) == []


def test_reprogramacion_rechazada_no_genera_evento(db, monkeypatch):
    cita = _cita(db)
    monkeypatch.setattr(
        citas.service_cu09,
        "consultar_disponibilidad",
        lambda *a, **k: SimpleNamespace(
            tiene_horario=True,
            intervalos_disponibles=[
                SimpleNamespace(hora_inicio=time(8, 0), hora_fin=time(9, 0))
            ],
        ),
    )

    with pytest.raises(HTTPException) as ctx:
        citas.reprogramar_cita(
            db,
            cita.id,
            CitaUpdate(fecha=DIA2, hora_inicio=time(15, 0)),
            _usuario(db),
        )

    assert ctx.value.status_code == 409
    assert _eventos(db) == []
    assert _notificaciones(db) == []


def test_retry_de_reprogramacion_no_duplica_y_transicion_posterior_si(db, monkeypatch):
    cita = _cita(db)
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )
    usuario = _usuario(db)

    citas.reprogramar_cita(
        db, cita.id, CitaUpdate(fecha=DIA2, hora_inicio=time(11, 0)), usuario,
    )
    citas.reprogramar_cita(
        db, cita.id, CitaUpdate(fecha=DIA2, hora_inicio=time(11, 0)), usuario,
    )
    assert _contar(db, CitaEvento) == 1

    citas.reprogramar_cita(
        db, cita.id, CitaUpdate(fecha=DIA3, hora_inicio=time(12, 0)), usuario,
    )
    eventos = _eventos(db)
    assert [e.tipo for e in eventos] == [
        "CITA_REPROGRAMADA", "CITA_REPROGRAMADA",
    ]
    assert evento_fechas(eventos[1]) == (DIA2, time(11, 0), DIA3, time(12, 0))
    assert _contar(db, Notificacion) == 2


def evento_fechas(evento):
    return (
        evento.fecha_anterior,
        evento.hora_anterior,
        evento.fecha_nueva,
        evento.hora_nueva,
    )


# =========================================================
# Cambio de estado
# =========================================================


def test_cambio_de_estado_registra_anterior_y_nuevo(db):
    cita = _cita(db)
    citas.cambiar_estado_cita(db, cita.id, "confirmada", _usuario(db))

    eventos = _eventos(db)
    assert len(eventos) == 1
    assert eventos[0].tipo == "CITA_ESTADO_CAMBIADO"
    assert eventos[0].estado_anterior == "PROGRAMADA"
    assert eventos[0].estado_nuevo == "CONFIRMADA"

    notificaciones = _notificaciones(db)
    assert len(notificaciones) == 1
    assert notificaciones[0].tipo == "CITA_CONFIRMADA"
    assert notificaciones[0].cuerpo == "El estado de tu cita fue actualizado."


def test_cambio_de_estado_no_op_no_genera_evento(db):
    cita = _cita(db)
    citas.cambiar_estado_cita(db, cita.id, "PROGRAMADA", _usuario(db))
    assert _eventos(db) == []
    assert _notificaciones(db) == []


def test_cambio_de_estado_invalido_no_genera_evento(db):
    cita = _cita(db)
    with pytest.raises(HTTPException) as ctx:
        citas.cambiar_estado_cita(db, cita.id, "NO_EXISTE", _usuario(db))
    assert ctx.value.status_code == 400
    assert _eventos(db) == []


def test_retry_de_estado_no_duplica_y_transicion_posterior_si(db):
    cita = _cita(db)
    usuario = _usuario(db)
    citas.cambiar_estado_cita(db, cita.id, "CONFIRMADA", usuario)
    citas.cambiar_estado_cita(db, cita.id, "CONFIRMADA", usuario)
    assert _contar(db, CitaEvento) == 1

    citas.cambiar_estado_cita(db, cita.id, "CANCELADA", usuario)
    eventos = _eventos(db)
    assert [(e.estado_anterior, e.estado_nuevo) for e in eventos] == [
        ("PROGRAMADA", "CONFIRMADA"),
        ("CONFIRMADA", "CANCELADA"),
    ]
    assert _contar(db, Notificacion) == 2


def test_cancelar_cita_tambien_genera_evento_de_estado(db):
    cita = _cita(db)
    citas.cancelar_cita(db, cita.id, _usuario(db))

    eventos = _eventos(db)
    assert len(eventos) == 1
    assert eventos[0].tipo == "CITA_ESTADO_CAMBIADO"
    assert eventos[0].estado_anterior == "PROGRAMADA"
    assert eventos[0].estado_nuevo == "CANCELADA"
    assert _notificaciones(db)[0].tipo == "CITA_CANCELADA"


# =========================================================
# Aislamiento y privacidad
# =========================================================


def test_dos_tenants_con_ids_iguales_no_cruzan_datos():
    engine_a = _crear_engine()
    engine_b = _crear_engine()
    try:
        with Session(engine_a) as db_a, Session(engine_b) as db_b:
            cita_a = _cita(db_a, paciente_id=1)
            cita_b = _cita(db_b, paciente_id=4)

            servicio.registrar_evento_cita_creada(db_a, cita_a, _usuario(db_a))
            db_a.commit()

            assert _contar(db_a, CitaEvento) == 1
            assert _contar(db_a, Notificacion) == 1
            assert db_a.scalar(select(Notificacion.usuario_id)) == 10

            assert _contar(db_b, CitaEvento) == 0
            assert _contar(db_b, Notificacion) == 0
    finally:
        engine_a.dispose()
        engine_b.dispose()


def test_notificacion_sin_pii_ni_texto_clinico(db, monkeypatch):
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )
    _registrar(db)

    notif = _notificaciones(db)[0]
    contenido = " ".join(
        [notif.titulo, notif.cuerpo, str(notif.datos or {})]
    ).lower()
    for prohibido in (
        "ana",
        "perez",
        "ci-001",
        "dolor ocular",
        "texto clinico privado",
        "observaciones",
        "diagnostico",
        "token",
        "fingerprint",
    ):
        assert prohibido not in contenido

    assert set((notif.datos or {}).keys()) <= CLAVES_DATOS_PERMITIDAS
    assert all(
        isinstance(valor, (str, int, bool))
        for valor in (notif.datos or {}).values()
    )


def test_clave_de_idempotencia_no_contiene_pii(db, monkeypatch):
    monkeypatch.setattr(
        citas.service_cu09, "consultar_disponibilidad", lambda *a, **k: _disponibilidad(),
    )
    _registrar(db)

    evento = _eventos(db)[0]
    assert len(evento.clave_idempotencia) <= 150
    assert evento.clave_idempotencia.startswith("CITA_CREADA:")
    for prohibido in ("Ana", "Perez", "CI-001", "Dolor", "token"):
        assert prohibido not in evento.clave_idempotencia
