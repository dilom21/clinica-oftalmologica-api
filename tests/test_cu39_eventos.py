from datetime import time

from sqlalchemy import select, text

from app.modules.notificaciones_interaccion_chatbot.models.notificacion import NotificacionPaciente
from tests.test_cu10_paciente_movil import DIA2, _body_cita, _cabecera, _token_paciente, cliente_citas
from tests.test_cu19_controles_medicos import DATOS, CONSULTAS, cliente_cu19, db_cu19


def _avisos(engine):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT paciente_id, tipo, mensaje, leida, clave_evento "
            "FROM notificacion_paciente ORDER BY id"
        )).all()


def test_cita_confirmacion_reprogramacion_cancelacion_y_reintentos(cliente_citas):
    client, _, engine = cliente_citas
    paciente = _cabecera(_token_paciente())
    cita = client.post("/agenda-citas/citas", json=_body_cita(inicio=time(10, 0)), headers=paciente).json()
    ruta = f"/agenda-citas/citas/{cita['id']}"
    # El cambio de estado autorizado se realiza desde recepción.
    from tests.test_cu10_paciente_movil import _token_recepcionista
    recepcion = _cabecera(_token_recepcionista())
    assert client.patch(f"{ruta}/estado", json={"estado": "CONFIRMADA"}, headers=recepcion).status_code == 200
    assert client.patch(f"{ruta}/estado", json={"estado": "CONFIRMADA"}, headers=recepcion).status_code == 200
    assert client.put(ruta, json={"fecha": DIA2.isoformat(), "hora_inicio": "09:00"}, headers=paciente).status_code == 200
    assert client.put(ruta, json={"fecha": DIA2.isoformat(), "hora_inicio": "09:00"}, headers=paciente).status_code == 200
    assert client.patch(f"{ruta}/estado", json={"estado": "CANCELADA"}, headers=paciente).status_code == 200
    assert client.patch(f"{ruta}/estado", json={"estado": "CANCELADA"}, headers=paciente).status_code == 200
    avisos = _avisos(engine)
    assert [a.tipo for a in avisos] == ["CITA_CONFIRMADA", "CITA_REPROGRAMADA", "CITA_CANCELADA"]
    assert all(a.paciente_id == 8 and not a.leida for a in avisos)
    assert DIA2.isoformat() in avisos[1].mensaje
    assert len({a.clave_evento for a in avisos}) == 3


def test_control_programado_y_fallo_sin_aviso(cliente_cu19, db_cu19):
    ruta = f"{CONSULTAS}/100/controles"
    assert cliente_cu19.post(ruta, json=DATOS).status_code == 201
    avisos = db_cu19.scalars(select(NotificacionPaciente)).all()
    assert len(avisos) == 1
    assert avisos[0].paciente_id == 1
    assert DATOS["fecha_programada"] in avisos[0].mensaje
    assert avisos[0].tipo == "CONTROL_PROGRAMADO"
    assert cliente_cu19.post(ruta, json={**DATOS, "fecha_programada": "1900-01-01"}).status_code == 422
    assert len(db_cu19.scalars(select(NotificacionPaciente)).all()) == 1


def test_fallo_y_rollback_de_cita_no_dejan_aviso(cliente_citas):
    client, db, _ = cliente_citas
    paciente = _cabecera(_token_paciente())
    cita = client.post("/agenda-citas/citas", json=_body_cita(inicio=time(10, 0)), headers=paciente).json()
    ruta = f"/agenda-citas/citas/{cita['id']}"
    fallo = client.put(ruta, json={"fecha": "1900-01-01", "hora_inicio": "09:00"}, headers=paciente)
    assert fallo.status_code == 400
    assert db.scalars(select(NotificacionPaciente)).all() == []
    db.rollback()
    db.connection().exec_driver_sql("BEGIN")
    from app.modules.notificaciones_interaccion_chatbot.services.notificaciones import crear_desde_evento
    crear_desde_evento(db, paciente_id=8, titulo="Temporal", mensaje="Temporal",
        tipo="CITA_REPROGRAMADA", clave_evento="rollback:1")
    db.rollback()
    assert db.scalars(select(NotificacionPaciente)).all() == []
