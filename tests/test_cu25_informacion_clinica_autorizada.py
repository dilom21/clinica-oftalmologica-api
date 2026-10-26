"""CU25 - El paciente consulta solo su información clínica autorizada."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session as session_mod
from app.main import app

URL = "/historial-clinico/mi-informacion"


def _esquema(engine):
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
        """CREATE TABLE paciente (
            id INTEGER PRIMARY KEY, usuario_id INTEGER UNIQUE REFERENCES usuario(id),
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
        """CREATE TABLE historial_clinico (
            id INTEGER PRIMARY KEY,
            paciente_id INTEGER NOT NULL UNIQUE REFERENCES paciente(id),
            fecha_apertura TIMESTAMP NOT NULL, observaciones_generales TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE antecedente_clinico (
            id INTEGER PRIMARY KEY,
            historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id),
            tipo VARCHAR(30) NOT NULL, descripcion TEXT NOT NULL,
            fecha_registro TIMESTAMP NOT NULL, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE consulta_clinica (
            id INTEGER PRIMARY KEY,
            historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id),
            cita_id INTEGER UNIQUE, oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
            fecha_consulta TIMESTAMP NOT NULL, motivo_consulta VARCHAR(255),
            anamnesis TEXT, observaciones TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE diagnostico (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
            nombre VARCHAR(150) NOT NULL, descripcion TEXT,
            fecha_diagnostico TIMESTAMP NOT NULL, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE tratamiento (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
            descripcion TEXT NOT NULL, observaciones TEXT,
            fecha_inicio DATE, fecha_fin DATE, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE indicacion (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
            descripcion TEXT NOT NULL, fecha_registro TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE receta (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
            fecha_emision TIMESTAMP NOT NULL, observaciones TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE detalle_receta (
            id INTEGER PRIMARY KEY,
            receta_id INTEGER NOT NULL REFERENCES receta(id),
            medicamento VARCHAR(150) NOT NULL, presentacion VARCHAR(100),
            dosis VARCHAR(100), frecuencia VARCHAR(100), duracion VARCHAR(100),
            indicaciones TEXT
        )""",
        """CREATE TABLE examen_oftalmologico (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
            nombre_examen VARCHAR(150) NOT NULL, fecha_solicitud TIMESTAMP NOT NULL,
            observaciones TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE resultado_examen (
            id INTEGER PRIMARY KEY,
            examen_id INTEGER NOT NULL REFERENCES examen_oftalmologico(id),
            fecha_resultado TIMESTAMP NOT NULL, resultado TEXT NOT NULL,
            archivo_url TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE control_medico (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER REFERENCES consulta_clinica(id),
            paciente_id INTEGER NOT NULL REFERENCES paciente(id),
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
            fecha_programada DATE NOT NULL, motivo VARCHAR(255),
            observaciones TEXT, estado VARCHAR(30) DEFAULT 'PROGRAMADO'
        )""",
    ]
    with engine.begin() as conn:
        for ddl in tablas:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(
            """INSERT INTO rol VALUES
                (1, 'Oftalmólogo', NULL, 1, 0, '2026-01-01'),
                (3, 'Recepcionista', NULL, 1, 0, '2026-01-01'),
                (4, 'Paciente', NULL, 1, 0, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO usuario VALUES
                (7, 'oftalmo@test', 'x', 1, '2026-01-01', 1),
                (9, 'recepcion@test', 'x', 1, '2026-01-01', 3),
                (10, 'ana@test', 'x', 1, '2026-01-01', 4),
                (14, 'luis@test', 'x', 1, '2026-01-01', 4),
                (15, 'inactivo@test', 'x', 1, '2026-01-01', 4),
                (16, 'sinperfil@test', 'x', 1, '2026-01-01', 4)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO paciente
                (id, usuario_id, nombres, apellidos, ci, fecha_registro, estado) VALUES
                (1, 10, 'Ana', 'Pérez', 'CI-001', '2026-09-01', 1),
                (2, 14, 'Luis', 'Gómez', 'CI-002', '2026-09-01', 1),
                (5, 15, 'Inés', 'Quiroga', 'CI-005', '2026-09-01', 0)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO oftalmologo
                (id, usuario_id, matricula, nombres, apellidos, especialidad,
                 estado, fecha_registro) VALUES
                (1, 7, 'MAT-001', 'Salet', 'Ejemplo', 'Glaucoma', 1, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO historial_clinico
                (id, paciente_id, fecha_apertura, observaciones_generales, estado) VALUES
                (10, 1, '2026-09-01 10:00:00', 'Usa lentes desde 2018', 1),
                (20, 2, '2026-09-01 10:00:00', 'Historial de otro paciente', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO antecedente_clinico
                (id, historial_clinico_id, tipo, descripcion, fecha_registro, estado) VALUES
                (1, 10, 'ALERGIA', 'Penicilina', '2026-09-01 10:00:00', 1),
                (2, 10, 'ENFERMEDAD', 'Antecedente retirado', '2026-09-02 10:00:00', 0),
                (3, 20, 'OTRO', 'Antecedente ajeno', '2026-09-01 10:00:00', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO consulta_clinica
                (id, historial_clinico_id, oftalmologo_id, fecha_consulta,
                 motivo_consulta, anamnesis, observaciones, estado) VALUES
                (100, 10, 1, '2026-10-01 15:00:00', 'Control de glaucoma',
                 'Presión ocular elevada', 'Continuar tratamiento', 1),
                (101, 10, 1, '2026-08-01 15:00:00', 'Consulta no autorizada',
                 NULL, NULL, 0),
                (200, 20, 1, '2026-10-02 15:00:00', 'Consulta de otro paciente',
                 NULL, NULL, 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO diagnostico
                (id, consulta_clinica_id, nombre, descripcion, fecha_diagnostico, estado) VALUES
                (1, 100, 'Glaucoma', 'Ojo derecho', '2026-10-01 16:00:00', 1),
                (2, 100, 'Diagnóstico retirado', NULL, '2026-10-01 16:05:00', 0),
                (3, 200, 'Miopía ajena', NULL, '2026-10-02 16:00:00', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO tratamiento
                (id, consulta_clinica_id, descripcion, observaciones,
                 fecha_inicio, fecha_fin, estado) VALUES
                (1, 100, 'Timolol 0.5%', 'Una gota cada 12 horas',
                 '2026-10-01', '2026-11-01', 1),
                (2, 100, 'Tratamiento retirado', NULL, NULL, NULL, 0)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO indicacion
                (id, consulta_clinica_id, descripcion, fecha_registro) VALUES
                (1, 100, 'No frotar los ojos', '2026-10-01 16:10:00')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO receta
                (id, consulta_clinica_id, fecha_emision, observaciones, estado) VALUES
                (1, 100, '2026-10-01 16:20:00', 'Receta vigente', 1),
                (2, 100, '2026-09-01 16:20:00', 'Receta anulada', 0)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO detalle_receta
                (id, receta_id, medicamento, presentacion, dosis, frecuencia, duracion, indicaciones)
                VALUES
                (1, 1, 'Timolol', 'Colirio', '0.5%', 'cada 12 horas', '30 días', 'Ojo derecho'),
                (2, 2, 'Medicamento anulado', NULL, NULL, NULL, NULL, NULL)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO examen_oftalmologico
                (id, consulta_clinica_id, nombre_examen, fecha_solicitud, observaciones, estado)
                VALUES
                (1, 100, 'Tonometría', '2026-10-01 15:30:00', NULL, 1),
                (2, 100, 'Examen anulado', '2026-10-01 15:40:00', NULL, 0)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO resultado_examen
                (id, examen_id, fecha_resultado, resultado, archivo_url, estado) VALUES
                (1, 1, '2026-10-01 17:00:00', 'PIO 22 mmHg', 'interno/archivo.pdf', 1),
                (2, 1, '2026-10-01 17:05:00', 'Resultado retirado', NULL, 0)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO control_medico
                (id, consulta_clinica_id, paciente_id, oftalmologo_id,
                 fecha_programada, motivo, observaciones, estado) VALUES
                (1, 100, 1, 1, '2026-11-01', 'Control de presión', NULL, 'PROGRAMADO'),
                (2, 100, 1, 1, '2026-09-01', 'Control cancelado', NULL, 'CANCELADO'),
                (3, 200, 2, 1, '2026-11-02', 'Control de otro paciente', NULL, 'PROGRAMADO')"""
        )


@pytest.fixture
def cliente_cu25():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _esquema(engine)
    db = Session(engine, autoflush=False)

    def _override():
        yield db

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session_mod.get_db] = _override
    app.dependency_overrides[dependencies.get_db] = _override

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
    app.dependency_overrides.update(anteriores)
    db.close()
    engine.dispose()


def _auth(usuario_id: int, rol_id: int) -> dict:
    return {"Authorization": f"Bearer {crear_access_token(usuario_id, rol_id)}"}


def test_paciente_ve_solo_su_informacion_vigente(cliente_cu25):
    respuesta = cliente_cu25.get(URL, headers=_auth(10, 4))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    texto = respuesta.text

    assert cuerpo["historial_id"] == 10
    assert cuerpo["observaciones_generales"] == "Usa lentes desde 2018"
    assert [item["descripcion"] for item in cuerpo["antecedentes"]] == ["Penicilina"]
    assert [item["id"] for item in cuerpo["consultas"]] == [100]
    consulta = cuerpo["consultas"][0]
    assert consulta["motivo_consulta"] == "Control de glaucoma"
    assert consulta["oftalmologo"]["nombres"] == "Salet"
    assert [item["nombre"] for item in consulta["diagnosticos"]] == ["Glaucoma"]
    assert [item["descripcion"] for item in consulta["tratamientos"]] == ["Timolol 0.5%"]
    assert consulta["indicaciones"][0]["descripcion"] == "No frotar los ojos"
    assert [item["id"] for item in consulta["recetas"]] == [1]
    assert consulta["recetas"][0]["detalles"][0]["medicamento"] == "Timolol"
    assert [item["nombre_examen"] for item in consulta["examenes"]] == ["Tonometría"]
    assert consulta["examenes"][0]["resultados"][0]["resultado"] == "PIO 22 mmHg"
    assert "archivo_url" not in consulta["examenes"][0]["resultados"][0]
    assert [item["motivo"] for item in cuerpo["controles"]] == ["Control de presión"]

    for ajeno in (
        "Historial de otro paciente",
        "Antecedente ajeno",
        "Consulta de otro paciente",
        "Consulta no autorizada",
        "Miopía ajena",
        "Diagnóstico retirado",
        "Tratamiento retirado",
        "Receta anulada",
        "Medicamento anulado",
        "Examen anulado",
        "Resultado retirado",
        "interno/archivo.pdf",
        "Control cancelado",
        "Control de otro paciente",
        "Antecedente retirado",
    ):
        assert ajeno not in texto


def test_otro_paciente_no_ve_el_historial_ajeno(cliente_cu25):
    respuesta = cliente_cu25.get(URL, headers=_auth(14, 4))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["historial_id"] == 20
    assert cuerpo["consultas"][0]["motivo_consulta"] == "Consulta de otro paciente"
    assert "Penicilina" not in respuesta.text
    assert "Control de glaucoma" not in respuesta.text


def test_personal_no_puede_usar_la_consulta_del_paciente(cliente_cu25):
    respuesta = cliente_cu25.get(URL, headers=_auth(9, 3))

    assert respuesta.status_code == 403
    assert "paciente" in respuesta.json()["detail"].lower()


def test_paciente_inactivo_recibe_403(cliente_cu25):
    respuesta = cliente_cu25.get(URL, headers=_auth(15, 4))

    assert respuesta.status_code == 403


def test_usuario_paciente_sin_perfil_recibe_404(cliente_cu25):
    respuesta = cliente_cu25.get(URL, headers=_auth(16, 4))

    assert respuesta.status_code == 404


def test_sin_token_no_consulta(cliente_cu25):
    respuesta = cliente_cu25.get(URL)

    assert respuesta.status_code in (401, 403)
