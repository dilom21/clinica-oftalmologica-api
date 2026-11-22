from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_db, obtener_usuario_actual
from app.main import app
from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.notificaciones_interaccion_chatbot.models.notificacion import NotificacionPaciente
from app.modules.notificaciones_interaccion_chatbot.services.notificaciones import crear_desde_evento


@pytest.fixture
def entorno():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Paciente.__table__.create(engine)
    NotificacionPaciente.__table__.create(engine)
    with Session(engine) as db:
        db.add_all([
            Paciente(id=1, usuario_id=11, nombres="Uno", apellidos="Paciente", fecha_registro=datetime.now(timezone.utc), estado=True),
            Paciente(id=2, usuario_id=22, nombres="Dos", apellidos="Paciente", fecha_registro=datetime.now(timezone.utc), estado=True),
        ])
        db.commit()
    actor = SimpleNamespace(id=11, rol=SimpleNamespace(nombre="Paciente"))
    def sesion():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = sesion
    app.dependency_overrides[obtener_usuario_actual] = lambda: actor
    try:
        yield TestClient(app), engine, actor
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _crear(db, paciente_id, clave, fecha):
    item = crear_desde_evento(db, paciente_id=paciente_id, titulo="Cita", mensaje="Recordatorio",
        tipo="CITA", clave_evento=clave, fecha_envio=fecha)
    item.fecha_creacion = fecha
    db.commit()
    return item.id


def test_lista_vacia_y_rol(entorno):
    client, _, actor = entorno
    assert client.get("/notificaciones/").json()["items"] == []
    actor.rol.nombre = "Administrador"
    assert client.get("/notificaciones/").status_code == 403


def test_paginacion_propiedad_lectura_e_idempotencia(entorno):
    client, engine, _ = entorno
    fecha = datetime(2026, 11, 21, tzinfo=timezone.utc)
    with Session(engine) as db:
        primero = _crear(db, 1, "cita:1", fecha)
        segundo = _crear(db, 1, "cita:2", fecha)
        ajeno = _crear(db, 2, "cita:3", fecha)
        assert crear_desde_evento(db, paciente_id=1, titulo="Otro", mensaje="Otro",
            tipo="CITA", clave_evento="cita:1").id == primero
    respuesta = client.get("/notificaciones/?limite=1&desplazamiento=0")
    assert respuesta.status_code == 200
    assert respuesta.json()["total"] == 2
    assert respuesta.json()["items"][0]["id"] == segundo
    assert client.get("/notificaciones/?limite=1&desplazamiento=1").json()["items"][0]["id"] == primero
    assert client.patch(f"/notificaciones/{ajeno}/leida").status_code == 404
    assert client.patch(f"/notificaciones/{primero}/leida").status_code == 200
    assert client.patch(f"/notificaciones/{primero}/leida").status_code == 200
    with Session(engine) as db:
        item = db.scalar(select(NotificacionPaciente).where(NotificacionPaciente.id == primero))
        assert item.leida and item.fecha_lectura is not None


def test_jwt_invalido_rechazado():
    with TestClient(app) as client:
        assert client.get("/notificaciones/", headers={"Authorization": "Bearer token-invalido"}).status_code == 401


def test_sesiones_de_clinicas_aisladas(entorno):
    client, primera, _ = entorno
    with Session(primera) as db:
        _crear(db, 1, "evento-a", datetime(2026, 11, 21, tzinfo=timezone.utc))
    segunda = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Paciente.__table__.create(segunda)
    NotificacionPaciente.__table__.create(segunda)
    with Session(segunda) as db:
        db.add(Paciente(id=1, usuario_id=11, nombres="Uno", apellidos="Paciente",
            fecha_registro=datetime.now(timezone.utc), estado=True))
        db.commit()
    def otra_sesion():
        with Session(segunda) as db:
            yield db
    app.dependency_overrides[get_db] = otra_sesion
    try:
        assert client.get("/notificaciones/").json()["items"] == []
    finally:
        segunda.dispose()
