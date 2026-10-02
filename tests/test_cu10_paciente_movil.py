# -*- coding: utf-8 -*-
"""CU10 - Acceso de pacientes (móvil): mismas tablas/endpoints que Web."""
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session as session_mod
from app.modules.gestion_agenda_citas.schemas.schemas import DURACION_CITA_MINUTOS
from app.main import app

DIA1 = date.today() + timedelta(days=7)
DIA2 = date.today() + timedelta(days=8)
AHORA = datetime.now(timezone.utc).replace(tzinfo=None)
WD1 = DIA1.isoweekday()
WD2 = DIA2.isoweekday()

DDL = [
    """CREATE TABLE rol (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL,
        descripcion TEXT, estado BOOLEAN NOT NULL, protegido BOOLEAN NOT NULL,
        fecha_creacion TIMESTAMP NOT NULL)""",
    """CREATE TABLE usuario (id INTEGER PRIMARY KEY, correo TEXT NOT NULL,
        password_hash TEXT NOT NULL, estado BOOLEAN NOT NULL,
        fecha_creacion TIMESTAMP NOT NULL,
        rol_id INTEGER NOT NULL REFERENCES rol(id))""",
    """CREATE TABLE funcion (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL,
        estado BOOLEAN NOT NULL)""",
    """CREATE TABLE accion (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL,
        estado BOOLEAN NOT NULL)""",
    """CREATE TABLE rol_funcion (id INTEGER PRIMARY KEY,
        rol_id INTEGER NOT NULL REFERENCES rol(id),
        funcion_id INTEGER NOT NULL REFERENCES funcion(id),
        accion_id INTEGER NOT NULL REFERENCES accion(id))""",
    """CREATE TABLE paciente (
        id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
        nombres TEXT NOT NULL, apellidos TEXT NOT NULL, ci TEXT,
        fecha_nacimiento DATE, sexo TEXT, telefono TEXT,
        contacto_emergencia TEXT, fecha_registro TIMESTAMP NOT NULL,
        direccion TEXT, estado BOOLEAN NOT NULL)""",
    """CREATE TABLE oftalmologo (id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuario(id),
        matricula TEXT NOT NULL, nombres TEXT NOT NULL, apellidos TEXT NOT NULL,
        especialidad TEXT, estado BOOLEAN NOT NULL,
        fecha_registro TIMESTAMP NOT NULL)""",
    """CREATE TABLE horario_oftalmologo (id INTEGER PRIMARY KEY,
        oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
        dia_semana INTEGER NOT NULL, hora_inicio TIME NOT NULL,
        hora_fin TIME NOT NULL, estado BOOLEAN NOT NULL)""",
    """CREATE TABLE bloqueo_horario (id INTEGER PRIMARY KEY,
        oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
        fecha DATE NOT NULL, hora_inicio TIME NOT NULL, hora_fin TIME NOT NULL,
        motivo TEXT, estado BOOLEAN NOT NULL, fecha_registro TIMESTAMP NOT NULL)""",
    """CREATE TABLE cita (
        id INTEGER PRIMARY KEY,
        paciente_id INTEGER NOT NULL REFERENCES paciente(id),
        oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
        fecha DATE NOT NULL, hora_inicio TIME NOT NULL, hora_fin TIME NOT NULL,
        motivo TEXT, observaciones TEXT, estado TEXT NOT NULL, canal TEXT,
        creado_por_usuario_id INTEGER REFERENCES usuario(id),
        fecha_registro TIMESTAMP NOT NULL, fecha_actualizacion TIMESTAMP NOT NULL)""",
    """CREATE TABLE bitacora (
        id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
        fecha_hora TIMESTAMP NOT NULL, ip TEXT, accion TEXT NOT NULL,
        entidad_afectada TEXT, id_registro_afectado INTEGER,
        descripcion TEXT)""",
]


@pytest.fixture
def cliente_citas():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        for ddl in DDL:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(
            "INSERT INTO rol VALUES (1,'Administrador',NULL,1,1,?),"
            "(2,'Recepcionista',NULL,1,0,?),(4,'Paciente',NULL,1,0,?)",
            (AHORA, AHORA, AHORA),
        )
        conn.exec_driver_sql(
            "INSERT INTO usuario VALUES "
            "(1,'admin@x.com','h',1,?,1),(2,'recep@x.com','h',1,?,2),"
            "(25,'paciente.a@x.com','h',1,?,4)",
            (AHORA, AHORA, AHORA),
        )
        conn.exec_driver_sql(
            "INSERT INTO accion VALUES (1,'LECTURA',1),(2,'ESCRITURA',1),(3,'AMBAS',1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO funcion VALUES "
            "(1,'Consultar agenda y disponibilidad médica',1),"
            "(2,'Gestionar citas médicas',1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO rol_funcion VALUES "
            "(1,2,1,3),(2,2,2,3),(3,1,1,3),(4,1,2,3)"
        )
        conn.exec_driver_sql(
            "INSERT INTO paciente VALUES "
            "(8,25,'Ana','Fernández','CI-08','1990-01-01','F','70000008',"
            "NULL,?,'Dir 8',1),"
            "(9,NULL,'Otro','Paciente','CI-09','1990-01-01','M','70000009',"
            "NULL,?,'Dir 9',1)",
            (AHORA, AHORA),
        )
        conn.exec_driver_sql(
            "INSERT INTO oftalmologo VALUES "
            "(1,2,'MAT-1','Salet','Ejemplo','Oftalmología General',1,?)",
            (AHORA,),
        )
        conn.exec_driver_sql(
            "INSERT INTO horario_oftalmologo VALUES "
            f"(1,1,{WD1},'08:00:00','13:00:00',1),"
            f"(2,1,{WD2},'08:00:00','13:00:00',1)"
        )
        # Cita existente de OTRO paciente (id 9) el DIA1 de 09:00 a 10:00.
        # Dato histórico previo a la regla de 30 minutos: debe seguir
        # ocupando horario (compatibilidad con citas ya registradas).
        conn.exec_driver_sql(
            "INSERT INTO cita VALUES "
            "(1,9,1,?,'09:00:00','10:00:00','Control',NULL,'PROGRAMADA',"
            "'WEB',2,?,?)",
            (DIA1, AHORA, AHORA),
        )

    db = Session(engine, autoflush=False)

    def _override():
        yield db

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session_mod.get_db] = _override
    app.dependency_overrides[dependencies.get_db] = _override

    with TestClient(app) as client:
        yield client, db, engine

    app.dependency_overrides.clear()
    app.dependency_overrides.update(anteriores)
    db.close()
    engine.dispose()


def _cabecera(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token_paciente() -> str:
    return crear_access_token(25, 4)


def _token_recepcionista() -> str:
    return crear_access_token(2, 2)


def _body_cita(fecha=DIA1, inicio=time(10, 0), paciente_id=8, oftalmologo_id=1):
    return {
        "paciente_id": paciente_id,
        "oftalmologo_id": oftalmologo_id,
        "fecha": fecha.isoformat(),
        "hora_inicio": inicio.strftime("%H:%M"),
        "motivo": "Consulta móvil",
        "observaciones": None,
    }


def _disponibles(client, token, fecha=DIA1, oftalmologo_id=1):
    r = client.get(
        "/agenda-citas/disponibilidad",
        params={"oftalmologo_id": oftalmologo_id, "fecha": fecha.isoformat()},
        headers=_cabecera(token),
    )
    assert r.status_code == 200
    return [
        (i["hora_inicio"], i["hora_fin"])
        for i in r.json()["intervalos_disponibles"]
    ]


def _minutos(valor: str) -> int:
    h, m, *_ = valor.split(":")
    return int(h) * 60 + int(m)


def _slot_libre(disponibles, inicio: time) -> bool:
    """True si hay un turno disponible que contenga el slot de la cita."""
    ini_slot = inicio.hour * 60 + inicio.minute
    fin_slot = ini_slot + DURACION_CITA_MINUTOS
    for ini, fin in disponibles:
        if _minutos(ini) <= ini_slot and fin_slot <= _minutos(fin):
            return True
    return False



# =========================================================
# Pruebas CU10 para paciente móvil
# =========================================================

def test_paciente_crea_cita_para_si_mismo_y_web_la_ve(cliente_citas):
    client, db, _ = cliente_citas
    token = _token_paciente()

    r = client.post("/agenda-citas/citas",
                    json=_body_cita(inicio=time(10, 0)),
                    headers=_cabecera(token))
    assert r.status_code == 201, r.text
    assert r.json()["paciente_id"] == 8
    assert r.json()["estado"] == "PROGRAMADA"
    # Duración fija: 10:00 -> 10:30
    assert r.json()["hora_inicio"] == "10:00:00"
    assert r.json()["hora_fin"] == "10:30:00"

    # La misma cita aparece para la recepcionista (misma tabla `cita`).
    rw = client.get("/agenda-citas/citas",
                    params={"fecha": DIA1.isoformat()},
                    headers=_cabecera(_token_recepcionista()))
    assert rw.status_code == 200
    assert r.json()["id"] in [c["id"] for c in rw.json()]

    # canal grabado por el backend (MOVIL).
    fila = db.execute(
        text("SELECT canal, estado FROM cita WHERE id = :cid"),
        {"cid": r.json()["id"]},
    ).first()
    assert fila[0] == "MOVIL"
    assert fila[1] == "PROGRAMADA"


def test_paciente_no_puede_crear_cita_para_otro(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()
    r = client.post("/agenda-citas/citas",
                    json=_body_cita(paciente_id=9, inicio=time(11, 0)),
                    headers=_cabecera(token))
    assert r.status_code == 403


def test_paciente_lista_solo_sus_citas(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()
    r = client.post("/agenda-citas/citas",
                    json=_body_cita(inicio=time(12, 0)),
                    headers=_cabecera(token))
    assert r.status_code == 201
    propia_id = r.json()["id"]

    rl = client.get("/agenda-citas/citas", headers=_cabecera(token))
    assert rl.status_code == 200
    ids = [c["id"] for c in rl.json()]
    assert propia_id in ids
    assert 1 not in ids  # cita del paciente 9

    # Intentar filtrar por otro paciente se rechaza.
    ro = client.get("/agenda-citas/citas",
                    params={"paciente_id": 9},
                    headers=_cabecera(token))
    assert ro.status_code == 403


def test_paciente_no_ve_detalle_de_cita_ajena(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()
    r = client.get("/agenda-citas/citas/1", headers=_cabecera(token))
    assert r.status_code == 404  # cita del paciente 9

    r2 = client.get("/agenda-citas/citas/2", headers=_cabecera(token))
    assert r2.status_code == 404  # inexistente


def test_paciente_reprograma_su_cita(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()
    creada = client.post("/agenda-citas/citas",
                         json=_body_cita(inicio=time(10, 0)),
                         headers=_cabecera(token)).json()

    r = client.put(
        f"/agenda-citas/citas/{creada['id']}",
        json={"fecha": DIA2.isoformat(), "hora_inicio": "09:00"},
        headers=_cabecera(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["fecha"] == DIA2.isoformat()
    assert r.json()["hora_inicio"] == "09:00:00"

    # No puede reprogramar cita ajena.
    ro = client.put(
        "/agenda-citas/citas/1",
        json={"fecha": DIA2.isoformat(), "hora_inicio": "08:00"},
        headers=_cabecera(token),
    )
    assert ro.status_code == 404



def test_paciente_cancela_su_cita_y_no_la_ajena(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()
    creada = client.post("/agenda-citas/citas",
                         json=_body_cita(inicio=time(12, 0)),
                         headers=_cabecera(token)).json()

    rc = client.patch(
        f"/agenda-citas/citas/{creada['id']}/estado",
        json={"estado": "CANCELADA"},
        headers=_cabecera(token),
    )
    assert rc.status_code == 200, rc.text
    assert rc.json()["estado"] == "CANCELADA"

    ra = client.patch(
        "/agenda-citas/citas/1/estado",
        json={"estado": "CANCELADA"},
        headers=_cabecera(token),
    )
    assert ra.status_code == 404


def test_paciente_no_administra_estados(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()
    creada = client.post("/agenda-citas/citas",
                         json=_body_cita(inicio=time(12, 0)),
                         headers=_cabecera(token)).json()

    for estado in ("ATENDIDA", "CONFIRMADA", "PROGRAMADA"):
        r = client.patch(
            f"/agenda-citas/citas/{creada['id']}/estado",
            json={"estado": estado},
            headers=_cabecera(token),
        )
        assert r.status_code == 403


def test_recepcionista_sigue_gestionando_citas(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_recepcionista()
    r = client.post("/agenda-citas/citas",
                    json=_body_cita(paciente_id=9, inicio=time(11, 0)),
                    headers=_cabecera(token))
    assert r.status_code == 201, r.text
    assert r.json()["paciente_id"] == 9

    re = client.patch(
        f"/agenda-citas/citas/{r.json()['id']}/estado",
        json={"estado": "CONFIRMADA"},
        headers=_cabecera(token),
    )
    assert re.status_code == 200
    assert re.json()["estado"] == "CONFIRMADA"


def test_ocupacion_y_liberacion_de_horario_cu09(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()

    disponibles_antes = _disponibles(client, token)
    assert _slot_libre(disponibles_antes, time(10, 0))

    creada = client.post("/agenda-citas/citas",
                         json=_body_cita(inicio=time(10, 0)),
                         headers=_cabecera(token)).json()

    # Móvil reserva 10:00-10:30 -> ya no está disponible.
    disponibles_tras = _disponibles(client, token)
    assert not _slot_libre(disponibles_tras, time(10, 0))

    # Cancelar libera el horario.
    client.patch(
        f"/agenda-citas/citas/{creada['id']}/estado",
        json={"estado": "CANCELADA"},
        headers=_cabecera(token),
    )
    disponibles_final = _disponibles(client, token)
    assert _slot_libre(disponibles_final, time(10, 0))


def test_cita_web_ocupa_horario_para_movil_y_horario_ocupado_se_rechaza(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()
    recep = _token_recepcionista()

    # Recepcionista reserva 11:00-11:30 (web).
    rw = client.post("/agenda-citas/citas",
                     json=_body_cita(paciente_id=9, inicio=time(11, 0)),
                     headers=_cabecera(recep))
    assert rw.status_code == 201

    # El paciente ya no puede tomar ese horario.
    rm = client.post("/agenda-citas/citas",
                     json=_body_cita(inicio=time(11, 0)),
                     headers=_cabecera(token))
    assert rm.status_code == 409

    # Tampoco un horario ya ocupado por la cita ajena 09:00-10:00.
    ro = client.post("/agenda-citas/citas",
                     json=_body_cita(inicio=time(9, 0)),
                     headers=_cabecera(token))
    assert ro.status_code == 409


def test_fecha_pasada_rechazada(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()
    r = client.post(
        "/agenda-citas/citas",
        json=_body_cita(fecha=date.today() - timedelta(days=1), inicio=time(10, 0)),
        headers=_cabecera(token),
    )
    assert r.status_code == 400



def test_turnos_consecutivos_de_30_minutos_permitidos(cliente_citas):
    """Dos citas consecutivas de 30 min (08:00-08:30 y 08:30-09:00)."""
    client, _, _ = cliente_citas
    recep = _token_recepcionista()

    r1 = client.post("/agenda-citas/citas",
                     json=_body_cita(paciente_id=9, inicio=time(8, 0)),
                     headers=_cabecera(recep))
    assert r1.status_code == 201, r1.text
    assert r1.json()["hora_inicio"] == "08:00:00"
    assert r1.json()["hora_fin"] == "08:30:00"

    r2 = client.post("/agenda-citas/citas",
                     json=_body_cita(paciente_id=9, inicio=time(8, 30)),
                     headers=_cabecera(recep))
    assert r2.status_code == 201, r2.text
    assert r2.json()["hora_inicio"] == "08:30:00"
    assert r2.json()["hora_fin"] == "09:00:00"


def test_solapamiento_de_30_minutos_rechazado(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()
    recep = _token_recepcionista()

    # Recepcionista ocupa 08:00-08:30.
    r1 = client.post("/agenda-citas/citas",
                     json=_body_cita(paciente_id=9, inicio=time(8, 0)),
                     headers=_cabecera(recep))
    assert r1.status_code == 201

    # El paciente intenta 08:15-08:45 (solapa con 08:00-08:30).
    r2 = client.post("/agenda-citas/citas",
                     json=_body_cita(inicio=time(8, 15)),
                     headers=_cabecera(token))
    assert r2.status_code == 409

    # También se rechaza un inicio dentro de una cita existente (09:30-10:00).
    r3 = client.post("/agenda-citas/citas",
                     json=_body_cita(inicio=time(9, 30)),
                     headers=_cabecera(token))
    assert r3.status_code == 409


def test_disponibilidad_devuelve_turnos_de_30_minutos(cliente_citas):
    client, _, _ = cliente_citas
    token = _token_paciente()

    disponibles = _disponibles(client, token)
    assert all(
        _minutos(fin) - _minutos(ini) == DURACION_CITA_MINUTOS
        for ini, fin in disponibles
    )
    assert _slot_libre(disponibles, time(10, 0))
    assert _slot_libre(disponibles, time(10, 30))

