"""CU19: contratos HTTP y seguridad con JWT real y SQLite aislado.

El esquema se crea solamente en memoria. La protección autouse de conftest
impide cualquier conexión a la base de datos PostgreSQL compartida.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_historial_clinico.models.controles import ControlMedico
from app.modules.gestion_historial_clinico.models.models import ConsultaClinica
from app.modules.gestion_historial_clinico.services import controles as service
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora


URL = "/historial-clinico/controles"
CONSULTAS = "/historial-clinico/consultas"
AHORA = datetime(2026, 10, 4, 12, tzinfo=ZoneInfo("America/La_Paz"))
DATOS = {
    "fecha_programada": "2026-10-10",
    "motivo": "Seguimiento de evolución",
    "observaciones": "Revisar agudeza visual",
}
OFTALMOLOGO = (7, 1)
OTRO_OFTALMOLOGO = (12, 1)


def _crear_esquema(engine):
    tablas = [
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
        """CREATE TABLE funcion (
            id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE accion (
            id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE rol_funcion (
            id INTEGER PRIMARY KEY, rol_id INTEGER NOT NULL REFERENCES rol(id),
            funcion_id INTEGER NOT NULL REFERENCES funcion(id),
            accion_id INTEGER NOT NULL REFERENCES accion(id),
            UNIQUE (rol_id, funcion_id)
        )""",
        """CREATE TABLE paciente (
            id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
            nombres TEXT NOT NULL, apellidos TEXT NOT NULL, ci TEXT,
            fecha_nacimiento DATE, sexo TEXT, telefono TEXT, contacto_emergencia TEXT,
            fecha_registro TIMESTAMP NOT NULL, direccion TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE oftalmologo (
            id INTEGER PRIMARY KEY,
            usuario_id INTEGER NOT NULL UNIQUE REFERENCES usuario(id),
            matricula TEXT NOT NULL UNIQUE, nombres TEXT NOT NULL,
            apellidos TEXT NOT NULL, especialidad TEXT, estado BOOLEAN NOT NULL,
            fecha_registro TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE cita (
            id INTEGER PRIMARY KEY, paciente_id INTEGER NOT NULL REFERENCES paciente(id),
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id), fecha DATE NOT NULL,
            hora_inicio TIME NOT NULL, hora_fin TIME NOT NULL, motivo TEXT,
            observaciones TEXT, estado TEXT NOT NULL, canal TEXT,
            creado_por_usuario_id INTEGER REFERENCES usuario(id),
            fecha_registro TIMESTAMP NOT NULL, fecha_actualizacion TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE historial_clinico (
            id INTEGER PRIMARY KEY,
            paciente_id INTEGER NOT NULL UNIQUE REFERENCES paciente(id),
            fecha_apertura TIMESTAMP NOT NULL, observaciones_generales TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE antecedente_clinico (
            id INTEGER PRIMARY KEY,
            historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id),
            tipo TEXT NOT NULL, descripcion TEXT NOT NULL,
            fecha_registro TIMESTAMP NOT NULL, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE consulta_clinica (
            id INTEGER PRIMARY KEY,
            historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id),
            cita_id INTEGER UNIQUE REFERENCES cita(id),
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
            fecha_consulta TIMESTAMP, motivo_consulta TEXT,
            anamnesis TEXT, observaciones TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE control_medico (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER REFERENCES consulta_clinica(id),
            paciente_id INTEGER NOT NULL REFERENCES paciente(id),
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
            fecha_programada DATE NOT NULL, motivo VARCHAR(255),
            observaciones TEXT, estado VARCHAR(30) DEFAULT 'PROGRAMADO'
        )""",
        """CREATE TABLE bitacora (
            id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
            fecha_hora TIMESTAMP NOT NULL, ip TEXT, accion TEXT NOT NULL,
            entidad_afectada TEXT, id_registro_afectado INTEGER, descripcion TEXT
        )""",
    ]
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in tablas:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(
            """INSERT INTO rol VALUES
                (1, 'Oftalmólogo', NULL, 1, 0, '2026-01-01'),
                (2, 'Administrador', NULL, 1, 1, '2026-01-01'),
                (3, 'Recepcionista', NULL, 1, 0, '2026-01-01'),
                (4, 'Paciente', NULL, 1, 0, '2026-01-01'),
                (5, 'Invitado', NULL, 1, 0, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO usuario VALUES
                (7, 'oftalmo@test', 'x', 1, '2026-01-01', 1),
                (8, 'admin@test', 'x', 1, '2026-01-01', 2),
                (9, 'recepcion@test', 'x', 1, '2026-01-01', 3),
                (10, 'paciente@test', 'x', 1, '2026-01-01', 4),
                (11, 'sinperfil@test', 'x', 1, '2026-01-01', 1),
                (12, 'otrooftalmo@test', 'x', 1, '2026-01-01', 1),
                (13, 'invitado@test', 'x', 1, '2026-01-01', 5)"""
        )
        conn.exec_driver_sql(
            "INSERT INTO accion VALUES (1, 'LECTURA', 1), (2, 'ESCRITURA', 1), (3, 'AMBAS', 1)"
        )
        conn.exec_driver_sql(
            """INSERT INTO funcion VALUES
                (15, 'Consultar historial clínico', 1),
                (19, 'Programar controles médicos', 1),
                (10, 'Gestionar citas médicas', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO rol_funcion VALUES
                (1, 1, 15, 1), (2, 1, 19, 2), (3, 1, 10, 1),
                (4, 2, 15, 1), (5, 2, 19, 2),
                (6, 3, 15, 1), (7, 3, 19, 2),
                (8, 4, 15, 1), (9, 4, 19, 2)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO paciente
                (id, nombres, apellidos, ci, fecha_registro, estado) VALUES
                (1, 'Ana', 'Pérez', 'CI-001', '2026-09-01', 1),
                (2, 'Luis', 'Gómez', 'CI-002', '2026-09-01', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO oftalmologo
                (id, usuario_id, matricula, nombres, apellidos, estado, fecha_registro) VALUES
                (1, 7, 'MAT-001', 'Salet', 'Ejemplo', 1, '2026-01-01'),
                (2, 12, 'MAT-002', 'Otro', 'Doctor', 1, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO cita
                (id, paciente_id, oftalmologo_id, fecha, hora_inicio, hora_fin,
                 estado, fecha_registro, fecha_actualizacion) VALUES
                (1, 1, 1, '2026-10-01', '08:00:00', '09:00:00',
                 'ATENDIDA', '2026-10-01', '2026-10-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO historial_clinico VALUES
                (10, 1, '2026-09-01', NULL, 1),
                (20, 2, '2026-09-01', NULL, 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO consulta_clinica VALUES
                (100, 10, 1, 1, '2026-10-01 12:00:00', 'Consulta previa', NULL, NULL, 1),
                (200, 20, NULL, 2, '2026-10-01 12:00:00', 'Otro doctor', NULL, NULL, 1),
                (300, 10, NULL, 1, '2026-10-01 12:00:00', 'Inactiva', NULL, NULL, 0),
                (400, 20, NULL, 1, '2026-10-02 12:00:00', 'Otra atención', NULL, NULL, 1)"""
        )


@pytest.fixture
def db_cu19(monkeypatch):
    monkeypatch.setattr(service, "_ahora_local", lambda: AHORA)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _crear_esquema(engine)
    with Session(engine, autoflush=False) as db:
        yield db
    engine.dispose()


@pytest.fixture
def cliente_cu19(db_cu19):
    def obtener_db_local():
        yield db_cu19

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = obtener_db_local
    app.dependency_overrides[dependencies.get_db] = obtener_db_local
    try:
        with TestClient(app) as client:
            autenticar(client)
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)


def autenticar(client, usuario=OFTALMOLOGO):
    client.headers["Authorization"] = f"Bearer {crear_access_token(*usuario)}"


def contar(db, modelo):
    return db.scalar(select(func.count()).select_from(modelo))


def programar(client, consulta_id=100, **cambios):
    respuesta = client.post(f"{CONSULTAS}/{consulta_id}/controles", json={**DATOS, **cambios})
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def test_programar_desde_atencion_deriva_relaciones_y_registra_bitacora(cliente_cu19, db_cu19):
    datos = programar(cliente_cu19)
    assert set(datos) == {
        "id", "consulta_clinica_id", "paciente_id", "oftalmologo_id",
        "fecha_programada", "motivo", "observaciones", "estado",
    }
    assert datos == {
        "id": datos["id"], "consulta_clinica_id": 100, "paciente_id": 1,
        "oftalmologo_id": 1, **DATOS, "estado": "PROGRAMADO",
    }
    control = db_cu19.get(ControlMedico, datos["id"])
    assert control.consulta_clinica_id == 100
    assert control.paciente_id == 1
    assert control.oftalmologo_id == 1
    bitacora = db_cu19.scalar(select(Bitacora))
    assert bitacora.usuario_id == 7
    assert bitacora.accion == "PROGRAMAR_CONTROL_MEDICO"
    assert bitacora.entidad_afectada == "control_medico"
    assert bitacora.id_registro_afectado == control.id
    assert cliente_cu19.get(f"{URL}/{control.id}").json() == datos


@pytest.mark.parametrize("body", [
    {}, {"motivo": "Seguimiento"}, {"fecha_programada": "2026-10-10"},
    {**DATOS, "motivo": ""}, {**DATOS, "motivo": " \t\n"},
    {**DATOS, "motivo": None}, {**DATOS, "motivo": "X" * 256},
    {**DATOS, "fecha_programada": None},
])
def test_campos_obligatorios_invalidos_no_persisten(cliente_cu19, db_cu19, body):
    respuesta = cliente_cu19.post(f"{CONSULTAS}/100/controles", json=body)
    assert respuesta.status_code == 422
    assert "detail" in respuesta.json()
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == 0


@pytest.mark.parametrize("campo,valor", [
    ("paciente_id", 2), ("oftalmologo_id", 2), ("consulta_clinica_id", 200),
    ("usuario_id", 12), ("cita_id", 1), ("estado", "REALIZADO"),
    ("campo_desconocido", "x"),
])
def test_post_rechaza_relaciones_y_estado_falsificados(cliente_cu19, db_cu19, campo, valor):
    respuesta = cliente_cu19.post(
        f"{CONSULTAS}/100/controles", json={**DATOS, campo: valor},
    )
    assert respuesta.status_code == 422
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == 0


@pytest.mark.parametrize("consulta_id", [999, 300])
def test_consulta_inexistente_o_inactiva(cliente_cu19, db_cu19, consulta_id):
    respuesta = cliente_cu19.post(f"{CONSULTAS}/{consulta_id}/controles", json=DATOS)
    assert respuesta.status_code == 404
    assert "consulta" in respuesta.json()["detail"].lower()
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == 0


@pytest.mark.parametrize("tabla,id_registro", [
    ("paciente", 1), ("historial_clinico", 10), ("oftalmologo", 1),
])
def test_contexto_clinico_inactivo_impide_programar(cliente_cu19, db_cu19, tabla, id_registro):
    db_cu19.execute(text(f"UPDATE {tabla} SET estado = 0 WHERE id = :id"), {"id": id_registro})
    db_cu19.commit()
    respuesta = cliente_cu19.post(f"{CONSULTAS}/100/controles", json=DATOS)
    assert respuesta.status_code in {403, 404, 409}
    assert isinstance(respuesta.json()["detail"], str)
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == 0


def test_consultar_controles_por_paciente_atencion_estado_y_trazabilidad(cliente_cu19, db_cu19):
    primero = programar(cliente_cu19)
    segundo = programar(cliente_cu19, motivo="Segundo seguimiento")
    tercero = programar(cliente_cu19, consulta_id=400)
    assert cliente_cu19.put(f"{URL}/{segundo['id']}", json={"estado": "CANCELADO"}).status_code == 200

    por_paciente = cliente_cu19.get(URL, params={"paciente_id": 1})
    assert por_paciente.status_code == 200
    assert {c["id"] for c in por_paciente.json()} == {primero["id"], segundo["id"]}
    por_atencion = cliente_cu19.get(f"{CONSULTAS}/100/controles")
    assert {c["id"] for c in por_atencion.json()} == {primero["id"], segundo["id"]}
    filtrado = cliente_cu19.get(URL, params={
        "paciente_id": 1, "consulta_clinica_id": 100, "estado": "CANCELADO",
    })
    assert [c["id"] for c in filtrado.json()] == [segundo["id"]]
    assert [c["id"] for c in cliente_cu19.get(URL, params={"paciente_id": 2}).json()] == [tercero["id"]]
    assert cliente_cu19.get(URL, params={"paciente_id": 1, "consulta_clinica_id": 400}).json() == []
    assert contar(db_cu19, ControlMedico) == 3


def test_actualizar_fecha_motivo_observaciones_sin_cambiar_relaciones(cliente_cu19, db_cu19):
    datos = programar(cliente_cu19)
    cambios = {"fecha_programada": "2026-10-12", "motivo": "  Nuevo motivo  ", "observaciones": None}
    respuesta = cliente_cu19.put(f"{URL}/{datos['id']}", json=cambios)
    assert respuesta.status_code == 200
    actualizado = respuesta.json()
    assert actualizado["fecha_programada"] == "2026-10-12"
    assert actualizado["motivo"] == "Nuevo motivo"
    assert actualizado["observaciones"] is None
    for campo in ("consulta_clinica_id", "paciente_id", "oftalmologo_id", "estado"):
        assert actualizado[campo] == datos[campo]
    assert cliente_cu19.get(f"{URL}/{datos['id']}").json() == actualizado
    bitacoras = db_cu19.scalars(select(Bitacora).order_by(Bitacora.id)).all()
    assert [b.accion for b in bitacoras] == ["PROGRAMAR_CONTROL_MEDICO", "ACTUALIZAR_CONTROL_MEDICO"]


@pytest.mark.parametrize("estado", ["REALIZADO", "CANCELADO"])
def test_actualizar_estado_de_control_vencido_conserva_fecha(cliente_cu19, db_cu19, estado):
    datos = programar(cliente_cu19)
    control = db_cu19.get(ControlMedico, datos["id"])
    control.fecha_programada = (AHORA - timedelta(days=1)).date()
    db_cu19.commit()
    respuesta = cliente_cu19.put(f"{URL}/{control.id}", json={"estado": estado})
    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == estado
    assert respuesta.json()["fecha_programada"] == "2026-10-03"
    assert contar(db_cu19, ControlMedico) == 1
    assert cliente_cu19.delete(f"{URL}/{control.id}").status_code == 405


def test_reprogramar_control_vencido_exige_fecha_valida(cliente_cu19, db_cu19):
    datos = programar(cliente_cu19)
    control = db_cu19.get(ControlMedico, datos["id"])
    control.fecha_programada = (AHORA - timedelta(days=1)).date()
    control.estado = "CANCELADO"
    db_cu19.commit()
    respuesta = cliente_cu19.put(f"{URL}/{control.id}", json={"estado": "PROGRAMADO"})
    assert respuesta.status_code == 422
    assert db_cu19.get(ControlMedico, control.id).estado == "CANCELADO"
    respuesta = cliente_cu19.put(f"{URL}/{control.id}", json={
        "estado": "PROGRAMADO", "fecha_programada": "2026-10-04",
    })
    assert respuesta.status_code == 200


@pytest.mark.parametrize("body", [
    {}, {"motivo": "  "}, {"motivo": None}, {"motivo": "X" * 256},
    {"fecha_programada": None}, {"estado": None}, {"estado": "PENDIENTE"},
    {"estado": True}, {"paciente_id": 2}, {"oftalmologo_id": 2},
    {"consulta_clinica_id": 400}, {"desconocido": "x"},
])
def test_put_invalido_no_modifica_control_ni_bitacora(cliente_cu19, db_cu19, body):
    datos = programar(cliente_cu19)
    respuesta = cliente_cu19.put(f"{URL}/{datos['id']}", json=body)
    assert respuesta.status_code == 422
    assert cliente_cu19.get(f"{URL}/{datos['id']}").json() == datos
    assert contar(db_cu19, Bitacora) == 1


@pytest.mark.parametrize("fecha", [
    "2026-10-03", "2026-02-30", "04/10/2026", "20261004", "2026-10-4",
    "2026-10-04T00:00:00Z", 1791072000, True, "",
])
def test_fecha_invalida_o_anterior_a_hoy(cliente_cu19, db_cu19, fecha):
    respuesta = cliente_cu19.post(f"{CONSULTAS}/100/controles", json={**DATOS, "fecha_programada": fecha})
    assert respuesta.status_code == 422
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == 0


def test_limite_del_dia_local_admite_hoy_aunque_utc_sea_manana(cliente_cu19, monkeypatch):
    monkeypatch.setattr(service, "_ahora_local", lambda: datetime(
        2026, 10, 4, 23, 30, tzinfo=ZoneInfo("America/La_Paz"),
    ))
    assert programar(cliente_cu19, fecha_programada="2026-10-04")["fecha_programada"] == "2026-10-04"


def test_fecha_de_consulta_utc_se_evalua_en_dia_local(cliente_cu19, db_cu19, monkeypatch):
    monkeypatch.setattr(service, "_ahora_local", lambda: datetime(
        2026, 10, 4, 23, 30, tzinfo=ZoneInfo("America/La_Paz"),
    ))
    consulta = db_cu19.get(ConsultaClinica, 100)
    consulta.fecha_consulta = datetime(2026, 10, 5, 2, tzinfo=timezone.utc)
    db_cu19.commit()
    assert programar(cliente_cu19, fecha_programada="2026-10-04")["consulta_clinica_id"] == 100


def test_atencion_futura_no_es_atencion_previa(cliente_cu19, db_cu19):
    consulta = db_cu19.get(ConsultaClinica, 100)
    consulta.fecha_consulta = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    db_cu19.commit()
    respuesta = cliente_cu19.post(f"{CONSULTAS}/100/controles", json=DATOS)
    assert respuesta.status_code in {409, 422}
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == 0


def test_atencion_legacy_sin_fecha_no_es_contexto_valido(cliente_cu19, db_cu19):
    db_cu19.execute(text("UPDATE consulta_clinica SET fecha_consulta = NULL WHERE id = 100"))
    db_cu19.commit()
    respuesta = cliente_cu19.post(f"{CONSULTAS}/100/controles", json=DATOS)
    assert respuesta.status_code == 409
    assert isinstance(respuesta.json()["detail"], str)
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == 0


@pytest.mark.parametrize("fecha", ["2026-10-03", "2026-10-10T00:00:00Z", 1791590400])
def test_actualizacion_fecha_invalida_no_modifica_control(cliente_cu19, db_cu19, fecha):
    datos = programar(cliente_cu19)
    respuesta = cliente_cu19.put(f"{URL}/{datos['id']}", json={"fecha_programada": fecha})
    assert respuesta.status_code == 422
    assert cliente_cu19.get(f"{URL}/{datos['id']}").json() == datos
    assert contar(db_cu19, Bitacora) == 1


@pytest.mark.parametrize("method,path,body", [
    ("post", f"{CONSULTAS}/100/controles", DATOS),
    ("get", URL, None), ("get", f"{CONSULTAS}/100/controles", None),
    ("get", f"{URL}/1", None), ("put", f"{URL}/1", {"estado": "REALIZADO"}),
    ("get", f"{URL}/oftalmologo-actual", None),
])
def test_sin_autenticacion_no_puede_acceder(cliente_cu19, method, path, body):
    cliente_cu19.headers.pop("Authorization")
    respuesta = cliente_cu19.request(method, path, json=body)
    # Mantiene el comportamiento HTTPBearer de la versión instalada.
    assert respuesta.status_code in {401, 403}


@pytest.mark.parametrize("usuario", [(8, 2), (9, 3), (10, 4), (13, 5), (11, 1)])
def test_rol_no_clinico_sin_permiso_o_sin_perfil_no_programa(cliente_cu19, db_cu19, usuario):
    autenticar(cliente_cu19, usuario)
    respuesta = cliente_cu19.post(f"{CONSULTAS}/100/controles", json=DATOS)
    assert respuesta.status_code == 403
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == 0


def test_permiso_escritura_insuficiente_para_lectura_y_viceversa(cliente_cu19, db_cu19):
    db_cu19.execute(text("UPDATE rol_funcion SET accion_id = 1 WHERE rol_id = 1 AND funcion_id = 19"))
    db_cu19.commit()
    assert cliente_cu19.post(f"{CONSULTAS}/100/controles", json=DATOS).status_code == 403
    db_cu19.execute(text("UPDATE rol_funcion SET accion_id = 2 WHERE rol_id = 1 AND funcion_id = 15"))
    db_cu19.commit()
    assert cliente_cu19.get(URL).status_code == 403


def test_permiso_ambas_y_rol_normalizado_reutilizan_autorizacion_existente(cliente_cu19, db_cu19):
    db_cu19.execute(text("UPDATE rol SET nombre = '  OFTALMÓLOGO  ' WHERE id = 1"))
    db_cu19.execute(text("UPDATE rol_funcion SET accion_id = 3 WHERE rol_id = 1 AND funcion_id IN (15, 19)"))
    db_cu19.commit()
    datos = programar(cliente_cu19)
    assert cliente_cu19.get(f"{URL}/{datos['id']}").status_code == 200


def test_token_invalido_y_usuario_inactivo_son_rechazados(cliente_cu19, db_cu19):
    cliente_cu19.headers["Authorization"] = "Bearer token-invalido"
    assert cliente_cu19.get(URL).status_code == 401
    autenticar(cliente_cu19)
    db_cu19.execute(text("UPDATE usuario SET estado = 0 WHERE id = 7"))
    db_cu19.commit()
    assert cliente_cu19.get(URL).status_code == 401


def test_rol_del_jwt_no_sustituye_permisos_reales_del_usuario(cliente_cu19, db_cu19):
    autenticar(cliente_cu19, (13, 1))  # Usuario Invitado, aunque el claim indique Oftalmólogo.
    assert cliente_cu19.post(f"{CONSULTAS}/100/controles", json=DATOS).status_code == 403
    assert cliente_cu19.get(URL).status_code == 403
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == 0


def test_contexto_oftalmologo_actual_reutiliza_usuario_y_perfil_activo(cliente_cu19, db_cu19):
    db_cu19.execute(text("UPDATE rol SET nombre = '  OFTALMÓLOGO  ' WHERE id = 1"))
    db_cu19.commit()
    respuesta = cliente_cu19.get(f"{URL}/oftalmologo-actual")
    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "id": 1, "matricula": "MAT-001", "nombres": "Salet",
        "apellidos": "Ejemplo", "especialidad": None,
    }
    autenticar(cliente_cu19, OTRO_OFTALMOLOGO)
    assert cliente_cu19.get(f"{URL}/oftalmologo-actual").json()["id"] == 2


@pytest.mark.parametrize("usuario", [(8, 2), (9, 3), (10, 4), (11, 1)])
def test_contexto_oftalmologo_null_para_otro_rol_o_sin_perfil(cliente_cu19, usuario):
    autenticar(cliente_cu19, usuario)
    respuesta = cliente_cu19.get(f"{URL}/oftalmologo-actual")
    assert respuesta.status_code == 200
    assert respuesta.json() is None


def test_contexto_oftalmologo_null_para_perfil_inactivo_y_protegido_por_permiso(cliente_cu19, db_cu19):
    db_cu19.execute(text("UPDATE oftalmologo SET estado = 0 WHERE id = 1"))
    db_cu19.commit()
    respuesta = cliente_cu19.get(f"{URL}/oftalmologo-actual")
    assert respuesta.status_code == 200
    assert respuesta.json() is None
    autenticar(cliente_cu19, (13, 5))
    assert cliente_cu19.get(f"{URL}/oftalmologo-actual").status_code == 403


def test_otro_oftalmologo_no_programa_ni_administra_atencion_ajena(cliente_cu19, db_cu19):
    datos = programar(cliente_cu19)
    autenticar(cliente_cu19, OTRO_OFTALMOLOGO)
    assert cliente_cu19.post(f"{CONSULTAS}/100/controles", json=DATOS).status_code == 403
    assert cliente_cu19.put(f"{URL}/{datos['id']}", json={"estado": "CANCELADO"}).status_code == 403
    assert cliente_cu19.get(f"{URL}/{datos['id']}").json() == datos
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == 1


def test_lectura_reutiliza_permiso_historial_sin_exigir_rol_oftalmologo(cliente_cu19):
    datos = programar(cliente_cu19)
    autenticar(cliente_cu19, (8, 2))
    assert cliente_cu19.get(f"{URL}/{datos['id']}").status_code == 200
    assert cliente_cu19.get(f"{CONSULTAS}/100/controles").status_code == 200
    assert cliente_cu19.get(URL, params={"paciente_id": 1}).status_code == 200
    autenticar(cliente_cu19, (13, 5))
    assert cliente_cu19.get(URL).status_code == 403


def test_control_inexistente_y_consulta_inexistente_en_lectura(cliente_cu19):
    assert cliente_cu19.get(f"{URL}/999").status_code == 404
    assert cliente_cu19.put(f"{URL}/999", json={"estado": "REALIZADO"}).status_code == 404
    assert cliente_cu19.get(f"{CONSULTAS}/999/controles").status_code == 404


@pytest.mark.parametrize("params", [
    {"paciente_id": 0}, {"consulta_clinica_id": -1},
    {"paciente_id": 2**63}, {"estado": "DESCONOCIDO"},
])
def test_filtros_invalidos_respetan_error_de_validacion(cliente_cu19, params):
    assert cliente_cu19.get(URL, params=params).status_code == 422


def test_registros_legacy_nulos_son_legibles_sin_inventar_consulta(cliente_cu19, db_cu19):
    db_cu19.execute(text("""INSERT INTO control_medico
        (id, consulta_clinica_id, paciente_id, oftalmologo_id, fecha_programada, motivo, estado)
        VALUES (50, NULL, 1, 1, '2026-09-10', NULL, NULL)"""))
    db_cu19.commit()
    respuesta = cliente_cu19.get(f"{URL}/50")
    assert respuesta.status_code == 200
    assert respuesta.json()["consulta_clinica_id"] is None
    assert respuesta.json()["motivo"] is None
    assert respuesta.json()["estado"] is None
    assert [c["id"] for c in cliente_cu19.get(URL, params={"paciente_id": 1}).json()] == [50]
    assert cliente_cu19.put(f"{URL}/50", json={"estado": "CANCELADO"}).status_code == 409
    assert contar(db_cu19, Bitacora) == 0


@pytest.mark.parametrize("campo,valor", [("paciente_id", 2), ("oftalmologo_id", 2)])
def test_control_legacy_con_relacion_incoherente_no_se_administra(cliente_cu19, db_cu19, campo, valor):
    datos = programar(cliente_cu19)
    db_cu19.execute(text(f"UPDATE control_medico SET {campo} = :valor WHERE id = :id"), {
        "valor": valor, "id": datos["id"],
    })
    db_cu19.commit()
    respuesta = cliente_cu19.put(f"{URL}/{datos['id']}", json={"estado": "CANCELADO"})
    assert respuesta.status_code == 409
    assert db_cu19.get(ControlMedico, datos["id"]).estado == "PROGRAMADO"
    assert contar(db_cu19, Bitacora) == 1


@pytest.mark.parametrize("operacion", ["post", "put"])
@pytest.mark.parametrize("fallo", ["bitacora", "commit", "flush", "refresh"])
def test_control_y_bitacora_son_atomicos_y_error_no_expone_detalles(
    cliente_cu19, db_cu19, monkeypatch, operacion, fallo,
):
    datos = programar(cliente_cu19) if operacion == "put" else None
    bitacora_real = service.registrar_bitacora

    def fallar(*args, **kwargs):
        raise SQLAlchemyError("Error técnico privado de persistencia")

    def bitacora_y_fallo(*args, **kwargs):
        bitacora_real(*args, **kwargs)
        fallar()

    with monkeypatch.context() as patch:
        if fallo == "bitacora":
            patch.setattr(service, "registrar_bitacora", bitacora_y_fallo)
        else:
            patch.setattr(db_cu19, fallo, fallar)
        if operacion == "post":
            respuesta = cliente_cu19.post(f"{CONSULTAS}/100/controles", json=DATOS)
        else:
            respuesta = cliente_cu19.put(f"{URL}/{datos['id']}", json={"motivo": "Cambio no confirmado"})
        assert respuesta.status_code == 500
        assert isinstance(respuesta.json()["detail"], str)
        assert "privado" not in respuesta.text
        assert "SQLAlchemy" not in respuesta.text

    esperado = int(operacion == "put")
    assert contar(db_cu19, ControlMedico) == contar(db_cu19, Bitacora) == esperado
    if datos:
        assert cliente_cu19.get(f"{URL}/{datos['id']}").json() == datos


def test_un_commit_por_operacion_cubre_control_y_bitacora(cliente_cu19, db_cu19, monkeypatch):
    commit = MagicMock(wraps=db_cu19.commit)
    monkeypatch.setattr(db_cu19, "commit", commit)
    datos = programar(cliente_cu19)
    commit.assert_called_once()
    commit.reset_mock()
    assert cliente_cu19.put(f"{URL}/{datos['id']}", json={"estado": "REALIZADO"}).status_code == 200
    commit.assert_called_once()
    assert contar(db_cu19, ControlMedico) == 1
    assert contar(db_cu19, Bitacora) == 2


def test_cu19_conserva_contratos_de_paciente_historial_cita_consulta_y_auth(cliente_cu19, db_cu19):
    rutas = [
        "/pacientes/1", "/historial-clinico/1",
        "/agenda-citas/citas/1", f"{CONSULTAS}/100", CONSULTAS,
    ]
    antes = {}
    for ruta in rutas:
        respuesta = cliente_cu19.get(ruta)
        assert respuesta.status_code == 200, respuesta.text
        antes[ruta] = respuesta.json()
    programar(cliente_cu19)
    for ruta in rutas:
        respuesta = cliente_cu19.get(ruta)
        assert respuesta.status_code == 200
        assert respuesta.json() == antes[ruta]
    assert db_cu19.execute(text("SELECT estado FROM cita WHERE id = 1")).scalar_one() == "ATENDIDA"
    cliente_cu19.headers["Authorization"] = "Bearer token-invalido"
    assert cliente_cu19.get(f"{CONSULTAS}/100").status_code == 401


@pytest.mark.parametrize("metodo,ruta,body,estado_esperado,tabla_clinica", [
    ("get", f"{URL}/oftalmologo-actual", None, 200, "oftalmologo"),
    ("get", URL, None, 200, "control_medico"),
    ("get", f"{CONSULTAS}/100/controles", None, 200, "control_medico"),
    ("get", f"{URL}/50", None, 200, "control_medico"),
    ("post", f"{CONSULTAS}/100/controles", DATOS, 201, "control_medico"),
    ("put", f"{URL}/50", {"estado": "REALIZADO"}, 200, "control_medico"),
])
def test_cu19_comparte_una_sesion_entre_jwt_permiso_y_operacion(
    db_cu19, metodo, ruta, body, estado_esperado, tabla_clinica,
):
    """Factories distintas detectan una segunda dependencia de sesión.

    No se reutiliza la sesión de la fixture como override: cada ejecución de
    un get_db abre una nueva Session. Así la prueba falla si CU19 vuelve a
    usar la dependencia separada de database.session, aunque ambas factories
    apunten al mismo motor SQLite aislado.
    """
    db_cu19.add(ControlMedico(
        id=50, consulta_clinica_id=100, paciente_id=1, oftalmologo_id=1,
        fecha_programada=datetime(2026, 10, 10).date(), motivo="Control previo",
        observaciones=None, estado="PROGRAMADO",
    ))
    db_cu19.commit()
    engine = db_cu19.get_bind()
    sesiones = []
    sesiones_vivas = set()

    def abrir_sesion(origen):
        db = Session(engine, autoflush=False)
        registro = {"db": db, "origen": origen, "cerrada": False, "consultas": []}
        sesiones.append(registro)
        sesiones_vivas.add(db)

        def observar_consulta(ejecucion):
            assert ejecucion.session is db
            assert db in sesiones_vivas
            registro["consultas"].append(str(ejecucion.statement))

        event.listen(db, "do_orm_execute", observar_consulta)
        try:
            yield db
        finally:
            db.close()
            registro["cerrada"] = True
            sesiones_vivas.remove(db)

    def obtener_db_auth_y_cu19():
        yield from abrir_sesion("auth_y_cu19")

    def obtener_db_router_separado():
        yield from abrir_sesion("router_separado")

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[dependencies.get_db] = obtener_db_auth_y_cu19
    app.dependency_overrides[session.get_db] = obtener_db_router_separado
    try:
        with TestClient(app) as client:
            autenticar(client)
            respuesta = client.request(metodo, ruta, json=body)
            assert respuesta.status_code == estado_esperado, respuesta.text
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)

    assert [registro["origen"] for registro in sesiones] == ["auth_y_cu19"]
    assert sesiones[0]["cerrada"] is True
    assert not sesiones_vivas
    consultas = sesiones[0]["consultas"]
    assert any("FROM usuario" in sql for sql in consultas), "La sesión debe validar el JWT real"
    assert any("FROM rol_funcion" in sql for sql in consultas), "La misma sesión debe validar permisos"
    assert any(tabla_clinica in sql for sql in consultas), "La operación clínica debe usar esa sesión"
    if metodo == "post":
        assert contar(db_cu19, ControlMedico) == 2
        assert contar(db_cu19, Bitacora) == 1
    elif metodo == "put":
        assert db_cu19.get(ControlMedico, 50).estado == "REALIZADO"
        assert contar(db_cu19, Bitacora) == 1
    else:
        assert contar(db_cu19, ControlMedico) == 1
        assert contar(db_cu19, Bitacora) == 0
