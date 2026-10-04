"""Pruebas unitarias CU15 - Registrar consulta clínica.

Fixture SQLite en memoria propio: no se conecta a la BD compartida ni genera
DDL desde los modelos SQLAlchemy.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.gestion_historial_clinico.models.models import ConsultaClinica
from app.modules.gestion_historial_clinico.repositories import repository as repo
from app.modules.gestion_historial_clinico.services import service
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora


URL = "/historial-clinico/consultas"

USUARIO_OFTALMOLOGO = (7, 1)
USUARIO_ADMIN = (8, 2)
USUARIO_RECEPCIONISTA = (9, 3)
USUARIO_PACIENTE = (10, 4)
USUARIO_OFTALMOLOGO_SIN_PERFIL = (11, 1)
USUARIO_INVITADO = (13, 5)

DATOS = {
    "historial_clinico_id": 10,
    "cita_id": 5,
    "motivo_consulta": "Dolor ocular",
    "anamnesis": "Paciente refiere molestia",
    "observaciones": "Sin alergias",
}


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
            paciente_id INTEGER NOT NULL UNIQUE REFERENCES paciente(id) ON DELETE RESTRICT,
            fecha_apertura TIMESTAMP NOT NULL, observaciones_generales TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE antecedente_clinico (
            id INTEGER PRIMARY KEY,
            historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id)
                ON DELETE CASCADE,
            tipo VARCHAR(30) NOT NULL CHECK (tipo IN (
                'ALERGIA', 'ENFERMEDAD', 'CIRUGIA', 'MEDICAMENTO',
                'ANTECEDENTE_FAMILIAR', 'OTRO'
            )),
            descripcion TEXT NOT NULL, fecha_registro TIMESTAMP NOT NULL,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE bitacora (
            id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
            fecha_hora TIMESTAMP NOT NULL, ip TEXT, accion TEXT NOT NULL,
            entidad_afectada TEXT, id_registro_afectado INTEGER, descripcion TEXT
        )""",
        """CREATE TABLE consulta_clinica (
            id INTEGER PRIMARY KEY,
            historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id),
            cita_id INTEGER UNIQUE REFERENCES cita(id),
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
            fecha_consulta TIMESTAMP NOT NULL, motivo_consulta VARCHAR(255),
            anamnesis TEXT, observaciones TEXT, estado BOOLEAN NOT NULL
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
                (12, 'oftalmo2@test', 'x', 1, '2026-01-01', 1),
                (13, 'invitado@test', 'x', 1, '2026-01-01', 5)"""
        )
        conn.exec_driver_sql(
            "INSERT INTO accion VALUES (1, 'LECTURA', 1), (2, 'ESCRITURA', 1), (3, 'AMBAS', 1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO funcion VALUES (15, 'Consultar historial clínico', 1), (17, 'Registrar consulta clínica', 1)"
        )
        conn.exec_driver_sql(
            """INSERT INTO rol_funcion VALUES
                (1, 1, 15, 1), (2, 1, 17, 2),
                (3, 2, 15, 1), (4, 2, 17, 2),
                (5, 3, 15, 1), (6, 3, 17, 2),
                (7, 4, 15, 1), (8, 4, 17, 2)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO paciente
                (id, usuario_id, nombres, apellidos, ci, fecha_registro, estado) VALUES
                (1, NULL, 'Ana', 'Pérez', 'CI-001', '2026-09-01', 1),
                (2, NULL, 'Luis', 'Gómez', 'CI-002', '2026-09-01', 1),
                (4, NULL, 'Inactivo', 'Hist', 'CI-004', '2026-09-01', 1),
                (5, NULL, 'Paciente', 'Inactivo', 'CI-005', '2026-09-01', 0)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO oftalmologo
                (id, usuario_id, matricula, nombres, apellidos, especialidad, estado, fecha_registro) VALUES
                (1, 7, 'MAT-001', 'Salet', 'Ejemplo', 'Oftalmología General', 1, '2026-01-01'),
                (2, 12, 'MAT-002', 'Otro', 'Doctor', 'Retina', 1, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO cita
                (id, paciente_id, oftalmologo_id, fecha, hora_inicio, hora_fin, estado,
                 fecha_registro, fecha_actualizacion) VALUES
                (1, 1, 1, '2026-09-01', '08:00:00', '09:00:00', 'ATENDIDA', '2026-09-01', '2026-09-01'),
                (2, 1, 2, '2026-09-01', '09:00:00', '10:00:00', 'ATENDIDA', '2026-09-01', '2026-09-01'),
                (3, 2, 1, '2026-09-02', '08:00:00', '09:00:00', 'PROGRAMADA', '2026-09-02', '2026-09-02'),
                (4, 1, 1, '2026-09-03', '08:00:00', '09:00:00', 'CONFIRMADA', '2026-09-03', '2026-09-03'),
                (5, 1, 1, '2026-09-04', '08:00:00', '09:00:00', 'PROGRAMADA', '2026-09-04', '2026-09-04'),
                (6, 1, 1, '2026-09-05', '08:00:00', '09:00:00', 'CANCELADA', '2026-09-05', '2026-09-05'),
                (7, 1, 1, '2026-09-06', '08:00:00', '09:00:00', 'NO_ASISTIO', '2026-09-06', '2026-09-06'),
                (8, 1, 1, '2026-09-07', '08:00:00', '09:00:00', 'CONFIRMADA', '2026-09-07', '2026-09-07'),
                (9, 1, 1, '2026-09-08', '08:00:00', '09:00:00', 'EN_ESPERA', '2026-09-08', '2026-09-08')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO historial_clinico
                (id, paciente_id, fecha_apertura, observaciones_generales, estado) VALUES
                (10, 1, '2026-09-01', NULL, 1),
                (20, 2, '2026-09-01', NULL, 1),
                (40, 4, '2026-09-01', NULL, 0),
                (50, 5, '2026-09-01', NULL, 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO consulta_clinica
                (id, historial_clinico_id, cita_id, oftalmologo_id, fecha_consulta,
                 motivo_consulta, anamnesis, observaciones, estado) VALUES
                (500, 10, 4, 1, '2026-09-03 08:00:00', 'Consulta previa', NULL, NULL, 1)"""
        )


@pytest.fixture
def db_cu15():
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
def cliente_cu15(db_cu15):
    def obtener_db_local():
        yield db_cu15

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = obtener_db_local
    app.dependency_overrides[dependencies.get_db] = obtener_db_local
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)


def autenticar(client, usuario):
    usuario_id, rol_id = usuario
    client.headers["Authorization"] = (
        f"Bearer {crear_access_token(usuario_id, rol_id)}"
    )


def contar(db, modelo):
    return db.scalar(select(func.count()).select_from(modelo))


def estado_cita(db, cita_id):
    return db.get(Cita, cita_id).estado


# =========================================================
# REGISTRO EXITOSO
# =========================================================


def test_post_registra_consulta_y_bitacora(cliente_cu15, db_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    assert estado_cita(db_cu15, 5) == "PROGRAMADA"
    respuesta = cliente_cu15.post(URL, json={
        "historial_clinico_id": 10,
        "cita_id": 5,
        "motivo_consulta": "  Dolor ocular  ",
        "anamnesis": "  Paciente refiere molestia  ",
        "observaciones": "  Sin alergias  ",
    })
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert set(datos) == {
        "id", "historial_clinico_id", "cita_id", "oftalmologo",
        "fecha_consulta", "motivo_consulta", "anamnesis", "observaciones",
        "estado",
    }
    assert datos["historial_clinico_id"] == 10
    assert datos["cita_id"] == 5
    assert datos["motivo_consulta"] == "Dolor ocular"
    assert datos["anamnesis"] == "Paciente refiere molestia"
    assert datos["observaciones"] == "Sin alergias"
    assert datos["estado"] is True
    assert datos["oftalmologo"] == {
        "id": 1, "matricula": "MAT-001", "nombres": "Salet",
        "apellidos": "Ejemplo", "especialidad": "Oftalmología General",
    }
    assert (
        datetime.fromisoformat(datos["fecha_consulta"]).date()
        == datetime.now(timezone.utc).date()
    )

    consulta = db_cu15.get(ConsultaClinica, datos["id"])
    assert consulta.historial_clinico_id == 10
    assert consulta.cita_id == 5
    assert consulta.oftalmologo_id == 1
    assert consulta.estado is True

    assert estado_cita(db_cu15, 5) == "ATENDIDA"

    bitacora = db_cu15.scalar(select(Bitacora))
    assert bitacora.usuario_id == 7
    assert bitacora.accion == "REGISTRAR_CONSULTA_CLINICA"
    assert bitacora.entidad_afectada == "consulta_clinica"
    assert bitacora.id_registro_afectado == consulta.id
    assert bitacora.descripcion == "Consulta clínica registrada"

    detalle = cliente_cu15.get(f"{URL}/{datos['id']}").json()
    assert detalle["id"] == datos["id"]


@pytest.mark.parametrize("cita_id", [5, 8, 9])
def test_post_cita_en_estado_admisible_pasa_a_atendida(
    cliente_cu15, db_cu15, cita_id,
):
    """PROGRAMADA, CONFIRMADA y EN_ESPERA admiten la atención."""
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={
        "historial_clinico_id": 10, "cita_id": cita_id,
    })
    assert respuesta.status_code == 201
    assert estado_cita(db_cu15, cita_id) == "ATENDIDA"


def test_post_sin_cita_es_valido(cliente_cu15, db_cu15, monkeypatch):
    actualizar = MagicMock(side_effect=AssertionError("no debe tocar citas"))
    monkeypatch.setattr(service.agenda_repo, "actualizar_estado_cita", actualizar)

    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={"historial_clinico_id": 10})
    assert respuesta.status_code == 201
    assert respuesta.json()["cita_id"] is None
    assert respuesta.json()["motivo_consulta"] is None
    assert contar(db_cu15, ConsultaClinica) == 2

    actualizar.assert_not_called()
    assert estado_cita(db_cu15, 5) == "PROGRAMADA"
    assert estado_cita(db_cu15, 8) == "CONFIRMADA"
    assert contar(db_cu15, Bitacora) == 1


def test_oftalmologo_id_del_body_es_rechazado(cliente_cu15, db_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={**DATOS, "oftalmologo_id": 2})
    assert respuesta.status_code == 422
    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0


# =========================================================
# SEGURIDAD Y ROLES
# =========================================================


@pytest.mark.parametrize("usuario", [
    USUARIO_ADMIN, USUARIO_RECEPCIONISTA, USUARIO_PACIENTE,
])
def test_solo_oftalmologo_puede_registrar(cliente_cu15, db_cu15, usuario):
    autenticar(cliente_cu15, usuario)
    respuesta = cliente_cu15.post(URL, json=DATOS)
    assert respuesta.status_code == 403
    assert "oftalmólogo" in respuesta.json()["detail"]
    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0


def test_rol_sin_permiso_es_rechazado(cliente_cu15, db_cu15):
    autenticar(cliente_cu15, USUARIO_INVITADO)
    respuesta = cliente_cu15.post(URL, json=DATOS)
    assert respuesta.status_code == 403
    assert "No tiene permiso" in respuesta.json()["detail"]
    assert contar(db_cu15, ConsultaClinica) == 1


def test_oftalmologo_sin_perfil_activo(cliente_cu15, db_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO_SIN_PERFIL)
    respuesta = cliente_cu15.post(URL, json=DATOS)
    assert respuesta.status_code == 403
    assert "perfil de oftalmólogo" in respuesta.json()["detail"]
    assert contar(db_cu15, ConsultaClinica) == 1


# =========================================================
# VALIDACIONES DE INTEGRIDAD
# =========================================================


@pytest.mark.parametrize("historial_id", [999, 40])
def test_historial_inexistente_o_inactivo(cliente_cu15, db_cu15, historial_id):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(
        URL, json={**DATOS, "historial_clinico_id": historial_id},
    )
    assert respuesta.status_code == 404
    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0


def test_cita_inexistente(cliente_cu15, db_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={**DATOS, "cita_id": 999})
    assert respuesta.status_code == 404
    assert contar(db_cu15, ConsultaClinica) == 1


def test_cita_de_otro_oftalmologo(cliente_cu15, db_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={**DATOS, "cita_id": 2})
    assert respuesta.status_code == 403
    assert contar(db_cu15, ConsultaClinica) == 1


def test_cita_de_otro_paciente(cliente_cu15, db_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={**DATOS, "cita_id": 3})
    assert respuesta.status_code == 409
    assert contar(db_cu15, ConsultaClinica) == 1


def test_cita_ya_tiene_consulta(cliente_cu15, db_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={**DATOS, "cita_id": 4})
    assert respuesta.status_code == 409
    assert "ya tiene una consulta" in respuesta.json()["detail"]
    assert contar(db_cu15, ConsultaClinica) == 1


# =========================================================
# ESTADO DE LA CITA (CORRECCIÓN 1)
# =========================================================


@pytest.mark.parametrize("cita_id, estado", [
    (6, "CANCELADA"),
    (7, "NO_ASISTIO"),
    (1, "ATENDIDA"),
])
def test_cita_en_estado_incompatible_es_rechazada(
    cliente_cu15, db_cu15, cita_id, estado,
):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={**DATOS, "cita_id": cita_id})
    assert respuesta.status_code == 409
    assert estado in respuesta.json()["detail"]
    assert estado_cita(db_cu15, cita_id) == estado
    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0


def test_cita_ya_atendida_no_registra_otra_consulta(cliente_cu15, db_cu15):
    """Cita ATENDIDA sin consulta previa: no se abre una segunda atención."""
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    primera = cliente_cu15.post(URL, json={**DATOS, "cita_id": 5})
    assert primera.status_code == 201
    assert estado_cita(db_cu15, 5) == "ATENDIDA"

    segunda = cliente_cu15.post(URL, json={**DATOS, "cita_id": 5})
    assert segunda.status_code == 409
    assert contar(db_cu15, ConsultaClinica) == 2
    assert contar(db_cu15, Bitacora) == 1


# =========================================================
# PACIENTE ACTIVO (CORRECCIÓN 5)
# =========================================================


def test_historial_de_paciente_inactivo_es_rechazado(cliente_cu15, db_cu15):
    """Historial activo (50) cuyo paciente (5) está inactivo."""
    assert db_cu15.get(Cita, 5) is not None
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={
        "historial_clinico_id": 50, "cita_id": 5,
    })
    assert respuesta.status_code == 409
    assert "inactivo" in respuesta.json()["detail"]
    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0
    assert estado_cita(db_cu15, 5) == "PROGRAMADA"


def test_historial_de_paciente_inactivo_sin_cita_es_rechazado(
    cliente_cu15, db_cu15,
):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={"historial_clinico_id": 50})
    assert respuesta.status_code == 409
    assert contar(db_cu15, ConsultaClinica) == 1


# =========================================================
# CARRERA ENTRE REGISTROS SIMULTÁNEOS (CORRECCIÓN 4)
# =========================================================


def test_unique_de_cita_id_devuelve_409(cliente_cu15, db_cu15, monkeypatch):
    """La validación previa puede quedar desfasada: el UNIQUE de la BD decide."""
    monkeypatch.setattr(repo, "obtener_consulta_por_cita_id", lambda *a, **k: None)

    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={**DATOS, "cita_id": 4})
    assert respuesta.status_code == 409
    assert "Ya existe una consulta clínica registrada para esta cita" in (
        respuesta.json()["detail"]
    )
    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0
    assert estado_cita(db_cu15, 4) == "CONFIRMADA"


@pytest.mark.parametrize("mensaje", [
    'duplicate key value violates unique constraint "uq_consulta_clinica_cita"',
    "UNIQUE constraint failed: consulta_clinica.cita_id",
])
def test_integrity_error_por_cita_se_traduce_a_409(mensaje):
    exc = IntegrityError("INSERT", {}, Exception(mensaje))
    assert service._es_violacion_unica_por_cita(exc)


@pytest.mark.parametrize("mensaje", [
    'duplicate key value violates unique constraint "uq_historial_clinico_paciente"',
    "FOREIGN KEY constraint failed",
    'duplicate key value violates unique constraint "otra_cosa"\n'
    "DETAIL:  Key (cita_id)=(4) already exists.",
])
def test_otros_integrity_error_no_se_traducen(mensaje):
    exc = IntegrityError("INSERT", {}, Exception(mensaje))
    assert not service._es_violacion_unica_por_cita(exc)


def test_otros_integrity_error_se_relanzan(cliente_cu15, db_cu15, monkeypatch):
    original = IntegrityError(
        "INSERT", {}, Exception("FOREIGN KEY constraint failed"),
    )

    def fallar(*args, **kwargs):
        raise original

    monkeypatch.setattr(repo, "crear_consulta_clinica", fallar)

    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    with pytest.raises(IntegrityError):
        cliente_cu15.post(URL, json=DATOS)

    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0
    assert estado_cita(db_cu15, 5) == "PROGRAMADA"


# =========================================================
# VALIDACIÓN DE CAMPOS
# =========================================================


@pytest.mark.parametrize("cambios", [
    {"historial_clinico_id": 0},
    {"historial_clinico_id": 2**63},
    {"historial_clinico_id": None},
    {"cita_id": 0},
    {"cita_id": -1},
    {"motivo_consulta": "X" * 256},
    {"usuario_id": 8},
    {"campo_desconocido": "x"},
])
def test_validacion_campos(cliente_cu15, db_cu15, cambios):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={**DATOS, **cambios})
    assert respuesta.status_code == 422
    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0


def test_texto_vacio_se_normaliza_a_nulo(cliente_cu15, db_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.post(URL, json={
        "historial_clinico_id": 10,
        "motivo_consulta": "   ",
        "anamnesis": "",
        "observaciones": "\t\n",
    })
    assert respuesta.status_code == 201
    assert respuesta.json()["motivo_consulta"] is None
    assert respuesta.json()["anamnesis"] is None
    assert respuesta.json()["observaciones"] is None


# =========================================================
# TRANSACCIONALIDAD
# =========================================================


@pytest.mark.parametrize("fallo", ["bitacora", "commit", "flush", "refresh"])
def test_rollback_atomico(cliente_cu15, db_cu15, monkeypatch, fallo):
    bitacora_real = service.registrar_bitacora

    def fallar(*args, **kwargs):
        raise SQLAlchemyError("Fallo de persistencia simulado")

    def bitacora_y_fallo(*args, **kwargs):
        bitacora_real(*args, **kwargs)
        fallar()

    with monkeypatch.context() as patch:
        if fallo == "bitacora":
            patch.setattr(service, "registrar_bitacora", bitacora_y_fallo)
        else:
            patch.setattr(db_cu15, fallo, fallar)
        autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
        with pytest.raises(SQLAlchemyError):
            cliente_cu15.post(URL, json=DATOS)

    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0
    assert estado_cita(db_cu15, 5) == "PROGRAMADA"


def test_rollback_si_falla_el_cambio_de_estado_de_cita(
    cliente_cu15, db_cu15, monkeypatch,
):
    """La consulta ya estaba en memoria cuando la cita falla al actualizarse:
    ningún efecto debe quedar persistido."""
    def fallar(*args, **kwargs):
        raise SQLAlchemyError("Fallo al actualizar el estado de la cita")

    monkeypatch.setattr(service.agenda_repo, "actualizar_estado_cita", fallar)

    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    with pytest.raises(SQLAlchemyError):
        cliente_cu15.post(URL, json=DATOS)

    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0
    assert estado_cita(db_cu15, 5) == "PROGRAMADA"


def test_un_solo_commit_cubre_consulta_cita_y_bitacora(db_cu15, monkeypatch):
    """Consulta + ATENDIDA + bitácora se confirman en un único commit."""
    from app.modules.gestion_historial_clinico.schemas.schemas import (
        ConsultaClinicaCrear,
    )
    from app.modules.gestion_usuarios_seguridad.models.models import Usuario

    commit = MagicMock(wraps=db_cu15.commit)
    monkeypatch.setattr(db_cu15, "commit", commit)

    service.registrar_consulta_clinica(
        db_cu15,
        ConsultaClinicaCrear(historial_clinico_id=10, cita_id=5),
        db_cu15.get(Usuario, 7),
    )

    commit.assert_called_once()
    assert contar(db_cu15, Bitacora) == 1
    assert estado_cita(db_cu15, 5) == "ATENDIDA"


def test_rollback_si_el_estado_de_cita_falla_no_deja_consulta(db_cu15, monkeypatch):
    """Si el UPDATE de la cita revienta, la consulta tampoco se guarda."""
    from app.modules.gestion_historial_clinico.schemas.schemas import (
        ConsultaClinicaCrear,
    )
    from app.modules.gestion_usuarios_seguridad.models.models import Usuario

    def fallar(*args, **kwargs):
        raise SQLAlchemyError("Fallo al actualizar el estado de la cita")

    monkeypatch.setattr(service.agenda_repo, "actualizar_estado_cita", fallar)

    with pytest.raises(SQLAlchemyError):
        service.registrar_consulta_clinica(
            db_cu15,
            ConsultaClinicaCrear(historial_clinico_id=10, cita_id=5),
            db_cu15.get(Usuario, 7),
        )

    assert contar(db_cu15, ConsultaClinica) == 1
    assert contar(db_cu15, Bitacora) == 0
    assert estado_cita(db_cu15, 5) == "PROGRAMADA"


def test_repositorio_sin_commit_y_fecha_utc():
    db = MagicMock()
    antes = datetime.now(timezone.utc)
    consulta = repo.crear_consulta_clinica(
        db,
        historial_clinico_id=10,
        cita_id=None,
        oftalmologo_id=1,
        motivo_consulta="Motivo",
        anamnesis=None,
        observaciones=None,
    )
    assert antes <= consulta.fecha_consulta <= datetime.now(timezone.utc)
    assert consulta.fecha_consulta.tzinfo == timezone.utc
    assert consulta.estado is True
    assert consulta.cita_id is None
    db.flush.assert_called_once()
    db.refresh.assert_called_once_with(consulta)
    db.commit.assert_not_called()


def test_service_confirma_una_sola_transaccion(db_cu15, monkeypatch):
    from app.modules.gestion_historial_clinico.schemas.schemas import (
        ConsultaClinicaCrear,
    )
    from app.modules.gestion_usuarios_seguridad.models.models import Usuario

    commit_real = db_cu15.commit
    commit = MagicMock(wraps=commit_real)
    monkeypatch.setattr(db_cu15, "commit", commit)

    service.registrar_consulta_clinica(
        db_cu15,
        ConsultaClinicaCrear(historial_clinico_id=10, cita_id=5),
        db_cu15.get(Usuario, 7),
    )
    commit.assert_called_once()
    assert contar(db_cu15, Bitacora) == 1


# =========================================================
# CONSULTAS
# =========================================================


def test_listar_y_filtrar_consultas(cliente_cu15, db_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    primera = cliente_cu15.post(URL, json={
        "historial_clinico_id": 10, "cita_id": 5,
    }).json()["id"]
    segunda = cliente_cu15.post(URL, json={
        "historial_clinico_id": 20,
    }).json()["id"]

    todas = [c["id"] for c in cliente_cu15.get(URL).json()]
    assert set(todas) == {segunda, primera, 500}
    assert todas.index(segunda) < todas.index(primera)

    por_historial = cliente_cu15.get(URL, params={"historial_clinico_id": 20}).json()
    assert [c["id"] for c in por_historial] == [segunda]

    por_paciente = cliente_cu15.get(URL, params={"paciente_id": 2}).json()
    assert [c["id"] for c in por_paciente] == [segunda]

    por_oftalmologo = [c["id"] for c in cliente_cu15.get(
        URL, params={"oftalmologo_id": 1},
    ).json()]
    assert set(por_oftalmologo) == {segunda, primera, 500}
    assert por_oftalmologo.index(segunda) < por_oftalmologo.index(primera)


def test_detalle_inexistente(cliente_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    assert cliente_cu15.get(f"{URL}/999").status_code == 404


def test_consulta_no_rompe_historial_clinico(cliente_cu15):
    autenticar(cliente_cu15, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu15.get("/historial-clinico/1")
    assert respuesta.status_code == 200
    assert respuesta.json()["historial"]["id"] == 10
