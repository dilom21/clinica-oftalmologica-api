from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.gestion_historial_clinico.models.controles import ControlMedico
from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.notificaciones_interaccion_chatbot.models.notificacion import Notificacion
from app.modules.notificaciones_interaccion_chatbot.services.recordatorios import (
    ConfiguracionRecordatorios, ZONA_CLINICA, procesar_tenant,
)
from app.modules.notificaciones_interaccion_chatbot import recordatorios_runner as runner


@pytest.fixture
def base():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for tabla in (Paciente, Cita, ControlMedico, Notificacion):
        tabla.__table__.create(engine)
    with Session(engine) as db:
        db.add_all([
            Paciente(id=1, usuario_id=11, nombres="Ana", apellidos="Uno",
                     fecha_registro=datetime.now(timezone.utc), estado=True),
            Paciente(id=2, usuario_id=22, nombres="Luis", apellidos="Dos",
                     fecha_registro=datetime.now(timezone.utc), estado=True),
        ])
        db.commit()
    yield engine
    engine.dispose()


def cita(db, id, paciente, fecha, hora, estado="CONFIRMADA"):
    db.add(Cita(id=id, paciente_id=paciente, oftalmologo_id=1, fecha=fecha,
        hora_inicio=hora, hora_fin=(datetime.combine(fecha, hora) + timedelta(minutes=30)).time(),
        estado=estado, fecha_registro=datetime.now(timezone.utc),
        fecha_actualizacion=datetime.now(timezone.utc)))


def control(db, id, paciente, fecha, estado="PROGRAMADO"):
    db.add(ControlMedico(id=id, paciente_id=paciente, oftalmologo_id=1,
        fecha_programada=fecha, estado=estado))


def avisos(db):
    return db.scalars(select(Notificacion).order_by(Notificacion.id)).all()


def test_ventanas_estados_destinatarios_y_repeticion(base):
    ahora = datetime(2026, 11, 21, 10, tzinfo=ZONA_CLINICA)
    with Session(base) as db:
        cita(db, 1, 1, date(2026, 11, 22), time(10, 1))  # fuera de 24 h
        cita(db, 2, 1, date(2026, 11, 22), time(10, 0))
        cita(db, 3, 2, date(2026, 11, 21), time(9, 0))  # ya inició
        cita(db, 4, 2, date(2026, 11, 21), time(11, 0), "CANCELADA")
        control(db, 5, 2, date(2026, 11, 22))
        control(db, 6, 1, date(2026, 11, 22), "CANCELADO")
        db.commit()
        assert procesar_tenant(db, ahora=ahora) == 2
        db.commit()
        assert procesar_tenant(db, ahora=ahora) == 0
        db.commit()
        assert [(n.usuario_id, n.tipo, n.datos) for n in avisos(db)] == [
            (11, "RECORDATORIO_CITA", None),
            (22, "ACTUALIZACION_OPERATIVA", {"subtipo": "RECORDATORIO_CONTROL"})]


def test_control_no_antes_de_umbral_y_cambio_de_cita(base):
    antes = datetime(2026, 11, 21, 8, 59, tzinfo=ZONA_CLINICA)
    with Session(base) as db:
        cita(db, 1, 1, date(2026, 11, 22), time(8, 59))
        control(db, 2, 1, date(2026, 11, 22))
        db.commit()
        assert procesar_tenant(db, ahora=antes) == 1
        db.commit()
        cita_actual = db.get(Cita, 1)
        cita_actual.fecha = date(2026, 11, 23)
        db.commit()
        assert procesar_tenant(db, ahora=antes + timedelta(minutes=1)) == 1
        db.commit()
        assert procesar_tenant(db, ahora=datetime(2026, 11, 22, 10, tzinfo=ZONA_CLINICA)) == 1
        db.commit()
        assert len({n.evento_clave for n in avisos(db)}) == 3


def test_rollback_y_fallo_de_tenant_no_contaminan_otro(base, monkeypatch):
    ahora = datetime(2026, 11, 21, 10, tzinfo=ZONA_CLINICA)
    with Session(base) as db:
        cita(db, 1, 1, date(2026, 11, 22), time(10))
        db.commit()
        db.connection().exec_driver_sql("BEGIN")  # SQLite: transacción real antes de SAVEPOINT.
        assert procesar_tenant(db, ahora=ahora) == 1
        db.rollback()
        assert avisos(db) == []

    class Control:
        def close(self): pass
    class Repo:
        def __init__(self, _db): pass
        def list_active_empresas(self):
            return [SimpleNamespace(codigo="SIN_MIGRAR"),
                    SimpleNamespace(codigo="FALLA"),
                    SimpleNamespace(codigo="ACTIVA")]
    class Resolver:
        def __init__(self, _repo): pass
        def resolver_por_codigo_empresa(self, codigo):
            version = "v4-sprint3-cu32-cu33" if codigo == "SIN_MIGRAR" else "v9-sprint5-revocacion-global-push"
            return SimpleNamespace(empresa_codigo=codigo, version_schema=version)
        def require_active_database(self, _contexto): pass
    class Registro:
        def get_session(self, contexto):
            if contexto.empresa_codigo == "FALLA":
                raise RuntimeError("tenant no disponible")
            return Session(base)
    monkeypatch.setattr(runner, "TenantControlPlaneRepository", Repo)
    monkeypatch.setattr(runner, "TenantResolver", Resolver)
    resultado = runner.ejecutar_una_vez(
        control_session_factory=Control, registry=Registro(), ahora=ahora)
    assert resultado == {"clinicas_ok": 1, "clinicas_omitidas": 1,
                         "clinicas_fallidas": 1, "recordatorios": 1}
    with Session(base) as db:
        assert len(avisos(db)) == 1


def test_configuracion_invalida():
    with pytest.raises(ValueError):
        ConfiguracionRecordatorios(horas_cita=0)


def test_control_cambia_de_fecha_y_cancelacion_posterior(base):
    inicio = datetime(2026, 11, 21, 10, tzinfo=ZONA_CLINICA)
    with Session(base) as db:
        control(db, 1, 1, date(2026, 11, 22))
        db.commit()
        assert procesar_tenant(db, ahora=inicio) == 1
        db.commit()
        item = db.get(ControlMedico, 1)
        item.fecha_programada = date(2026, 11, 23)
        db.commit()
        assert procesar_tenant(db, ahora=inicio) == 0
        assert procesar_tenant(db, ahora=inicio + timedelta(days=1)) == 1
        db.commit()
        item.estado = "CANCELADO"
        db.commit()
        assert procesar_tenant(db, ahora=inicio + timedelta(days=1)) == 0
        db.commit()
        assert len(avisos(db)) == 2  # historial anterior conservado
