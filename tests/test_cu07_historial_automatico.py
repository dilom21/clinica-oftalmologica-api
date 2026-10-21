"""Pruebas CU07 / registro móvil: historial clínico automático (1 a 1).

Cubre la regularización previa a CU17:
- CU07 (POST /pacientes) crea Paciente + HistorialClinico + bitácora en un
  único commit.
- POST /seguridad/registro-paciente asegura el historial en los dos
  escenarios (paciente nuevo y vinculación a un paciente presencial).
- La operación de asegurado es idempotente y no reactiva historiales
  inactivos ni crea duplicados.
- CU13, CU15 y CU16 siguen funcionando con el historial generado.

Fixture SQLite en memoria propio: no se conecta a la BD compartida ni genera
DDL desde los modelos SQLAlchemy.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_historial_clinico.models.models import (
    ConsultaClinica,
    Diagnostico,
    HistorialClinico,
)
from app.modules.gestion_historial_clinico.repositories import (
    repository as repo_historial,
)
from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.gestion_pacientes.schemas.schemas import PacienteCrear
from app.modules.gestion_pacientes.services import service as service_pacientes
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora, Usuario
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    RegistroPacienteRequest,
)
from app.modules.gestion_usuarios_seguridad.services import (
    registro_paciente_service,
)


URL_PACIENTES = "/pacientes"
URL_REGISTRO_MOVIL = "/seguridad/registro-paciente"
URL_CONSULTAS = "/historial-clinico/consultas"

USUARIO_OFTALMOLOGO = (7, 1)

PACIENTES_SEMBRADOS = 4
HISTORIALES_SEMBRADOS = 2
USUARIOS_SEMBRADOS = 1

PACIENTE_CON_HISTORIAL = 1
PACIENTE_SIN_HISTORIAL = 3
PACIENTE_HISTORIAL_INACTIVO = 4

DATOS_PACIENTE = {
    "nombres": "Nuevo",
    "apellidos": "Paciente",
    "ci": "CI-900",
    "fecha_nacimiento": "1995-04-18",
    "sexo": "M",
    "telefono": "70090090",
    "direccion": "Av. Siempre Viva 100",
}

DATOS_REGISTRO_NUEVO = {
    "ci": "CI-901",
    "nombres": "Movil",
    "apellidos": "Nuevo",
    "fecha_nacimiento": "1998-07-07",
    "sexo": "F",
    "telefono": "70080807",
    "correo": "movil.nuevo@example.test",
    "password": "ClaveSegura123!",
}

# Paciente presencial sembrado SIN historial (id 3).
DATOS_VINCULACION_SIN_HISTORIAL = {
    "ci": "CI-003",
    "nombres": "Presencial",
    "apellidos": "Sin Historial",
    "fecha_nacimiento": "1990-05-20",
    "sexo": "M",
    "telefono": "77712345",
    "correo": "vinculado.sin.historial@example.test",
    "password": "ClaveSegura123!",
}

# Paciente presencial sembrado CON historial activo (id 1).
DATOS_VINCULACION_CON_HISTORIAL = {
    "ci": "CI-001",
    "nombres": "Ana",
    "apellidos": "Pérez",
    "fecha_nacimiento": "1985-03-10",
    "sexo": "F",
    "telefono": "70000001",
    "correo": "vinculado.con.historial@example.test",
    "password": "ClaveSegura123!",
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
        """CREATE TABLE diagnostico (
            id INTEGER PRIMARY KEY,
            consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id)
                ON DELETE CASCADE,
            nombre VARCHAR(150) NOT NULL, descripcion TEXT,
            fecha_diagnostico TIMESTAMP NOT NULL, estado BOOLEAN NOT NULL
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
                (4, 'Paciente', NULL, 1, 0, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO usuario VALUES
                (7, 'oftalmo@test', 'x', 1, '2026-01-01', 1)"""
        )
        conn.exec_driver_sql(
            "INSERT INTO accion VALUES (1, 'LECTURA', 1), (2, 'ESCRITURA', 1), (3, 'AMBAS', 1)"
        )
        conn.exec_driver_sql(
            """INSERT INTO funcion VALUES
                (15, 'Consultar historial clínico', 1),
                (17, 'Registrar consulta clínica', 1),
                (18, 'Registrar diagnóstico', 1)"""
        )
        conn.exec_driver_sql(
            "INSERT INTO rol_funcion VALUES (1, 1, 15, 1), (2, 1, 17, 2), (3, 1, 18, 2)"
        )
        conn.exec_driver_sql(
            """INSERT INTO paciente
                (id, usuario_id, nombres, apellidos, ci, fecha_nacimiento, telefono,
                 fecha_registro, estado) VALUES
                (1, NULL, 'Ana', 'Perez', 'CI-001', '1985-03-10', '70000001', '2026-09-01', 1),
                (3, NULL, 'Presencial', 'Sin Historial', 'CI-003', '1990-05-20', '77712345', '2026-09-02', 1),
                (4, NULL, 'Historial', 'Inactivo', 'CI-004', NULL, NULL, '2026-09-03', 1),
                (5, NULL, 'Paciente', 'Inactivo', 'CI-005', NULL, NULL, '2026-09-04', 0)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO oftalmologo
                (id, usuario_id, matricula, nombres, apellidos, especialidad, estado, fecha_registro)
                VALUES (1, 7, 'MAT-001', 'Salet', 'Ejemplo', 'Oftalmologia General', 1, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO historial_clinico
                (id, paciente_id, fecha_apertura, observaciones_generales, estado) VALUES
                (10, 1, '2026-09-01', NULL, 1),
                (40, 4, '2026-09-03', NULL, 0)"""
        )


@pytest.fixture
def db_historial_auto():
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
def cliente_historial_auto(db_historial_auto):
    def obtener_db_local():
        yield db_historial_auto

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


def contar_historiales(db, paciente_id):
    return db.scalar(
        select(func.count())
        .select_from(HistorialClinico)
        .where(HistorialClinico.paciente_id == paciente_id)
    )


def acciones_bitacora(db):
    return [registro.accion for registro in db.scalars(select(Bitacora)).all()]


def sin_zona(valor: datetime) -> datetime:
    """Normaliza a naive: SQLite devuelve los DateTime sin zona."""
    return valor.replace(tzinfo=None)


def crear_paciente_via_cu07(client) -> int:
    respuesta = client.post(URL_PACIENTES, json=DATOS_PACIENTE)
    assert respuesta.status_code == 201
    return respuesta.json()["id"]

# =========================================================
# CU07 - REGISTRO PRESENCIAL: PACIENTE + HISTORIAL + BITÁCORA
# =========================================================


def test_cu07_crea_paciente_con_su_historial_en_un_solo_commit(
    cliente_historial_auto, db_historial_auto,
):
    cliente = cliente_historial_auto
    db = db_historial_auto

    paciente_id = crear_paciente_via_cu07(cliente)
    paciente = db.get(Paciente, paciente_id)

    assert paciente is not None
    assert contar(db, Paciente) == PACIENTES_SEMBRADOS + 1

    historial = repo_historial.obtener_historial_por_paciente_id(db, paciente_id)
    assert historial is not None
    assert historial.paciente_id == paciente_id
    assert historial.estado is True
    assert historial.observaciones_generales is None
    assert sin_zona(historial.fecha_apertura) == sin_zona(paciente.fecha_registro)

    assert contar_historiales(db, paciente_id) == 1
    assert contar(db, HistorialClinico) == HISTORIALES_SEMBRADOS + 1
    assert acciones_bitacora(db) == ["CREAR_PACIENTE"]


def test_cu07_no_crea_historial_para_pacientes_sembrados(
    cliente_historial_auto, db_historial_auto,
):
    """El asegurado solo depende del paciente nuevo, no de otros registros."""
    crear_paciente_via_cu07(cliente_historial_auto)
    db = db_historial_auto

    assert contar_historiales(db, PACIENTE_SIN_HISTORIAL) == 0
    assert contar_historiales(db, PACIENTE_CON_HISTORIAL) == 1
    assert contar_historiales(db, PACIENTE_HISTORIAL_INACTIVO) == 1


def test_rollback_en_cu07_si_falla_la_creacion_del_historial(
    db_historial_auto, monkeypatch,
):
    db = db_historial_auto

    def explotar(*args, **kwargs):
        raise RuntimeError("fallo al crear el historial clínico")

    monkeypatch.setattr(repo_historial, "crear_historial_clinico", explotar)

    with pytest.raises(RuntimeError):
        service_pacientes.crear_paciente(db, PacienteCrear(**DATOS_PACIENTE))

    # Ni paciente, ni historial, ni bitácora: todo revertido.
    assert contar(db, Paciente) == PACIENTES_SEMBRADOS
    assert db.scalar(
        select(Paciente).where(Paciente.ci == DATOS_PACIENTE["ci"])
    ) is None
    assert contar(db, HistorialClinico) == HISTORIALES_SEMBRADOS
    assert contar(db, Bitacora) == 0


# =========================================================
# EL HISTORIAL AUTOMÁTICO ALIMENTA CU13, CU15 Y CU16
# =========================================================


def test_el_historial_de_cu07_alimenta_cu13(
    cliente_historial_auto, db_historial_auto,
):
    paciente_id = crear_paciente_via_cu07(cliente_historial_auto)
    autenticar(cliente_historial_auto, USUARIO_OFTALMOLOGO)

    respuesta = cliente_historial_auto.get(f"/historial-clinico/{paciente_id}")
    assert respuesta.status_code == 200

    datos = respuesta.json()
    assert datos["paciente"]["id"] == paciente_id
    assert datos["historial"] is not None
    assert datos["historial"]["antecedentes"] == []
    assert datos["historial"]["observaciones_generales"] is None
    assert datos["historial"]["fecha_apertura"] == datos["paciente"]["fecha_registro"]


def test_el_historial_de_cu07_alimenta_cu15_y_cu16(
    cliente_historial_auto, db_historial_auto,
):
    db = db_historial_auto
    paciente_id = crear_paciente_via_cu07(cliente_historial_auto)
    historial = repo_historial.obtener_historial_por_paciente_id(db, paciente_id)
    assert historial is not None

    autenticar(cliente_historial_auto, USUARIO_OFTALMOLOGO)

    consulta = cliente_historial_auto.post(
        URL_CONSULTAS,
        json={
            "historial_clinico_id": historial.id,
            "motivo_consulta": "Control post-operatorio",
        },
    )
    assert consulta.status_code == 201
    consulta_id = consulta.json()["id"]
    assert consulta.json()["historial_clinico_id"] == historial.id

    diagnostico = cliente_historial_auto.post(
        f"{URL_CONSULTAS}/{consulta_id}/diagnosticos",
        json={"nombre": "Miopía leve", "descripcion": "Ojo derecho"},
    )
    assert diagnostico.status_code == 201

    listado = cliente_historial_auto.get(
        f"{URL_CONSULTAS}/{consulta_id}/diagnosticos"
    )
    assert listado.status_code == 200
    assert [d["nombre"] for d in listado.json()] == ["Miopía leve"]

    assert contar(db, ConsultaClinica) == 1
    assert contar(db, Diagnostico) == 1


# =========================================================
# REGISTRO MÓVIL (POST /seguridad/registro-paciente)
# =========================================================


def test_registro_movil_paciente_nuevo_crea_usuario_paciente_e_historial(
    cliente_historial_auto, db_historial_auto,
):
    db = db_historial_auto

    respuesta = cliente_historial_auto.post(
        URL_REGISTRO_MOVIL, json=DATOS_REGISTRO_NUEVO,
    )
    assert respuesta.status_code == 201

    usuario = db.scalar(
        select(Usuario).where(Usuario.correo == DATOS_REGISTRO_NUEVO["correo"])
    )
    assert usuario is not None
    assert usuario.estado is True

    paciente = db.scalar(select(Paciente).where(Paciente.usuario_id == usuario.id))
    assert paciente is not None
    assert paciente.ci == DATOS_REGISTRO_NUEVO["ci"]

    historial = repo_historial.obtener_historial_por_paciente_id(db, paciente.id)
    assert historial is not None
    assert historial.estado is True
    assert historial.observaciones_generales is None
    assert sin_zona(historial.fecha_apertura) == sin_zona(paciente.fecha_registro)

    assert contar(db, Usuario) == USUARIOS_SEMBRADOS + 1
    assert contar(db, Paciente) == PACIENTES_SEMBRADOS + 1
    assert contar(db, HistorialClinico) == HISTORIALES_SEMBRADOS + 1
    assert acciones_bitacora(db) == ["REGISTRO_PACIENTE"]


def test_registro_movil_vincula_paciente_sin_historial_y_lo_crea(
    cliente_historial_auto, db_historial_auto,
):
    db = db_historial_auto
    assert contar_historiales(db, PACIENTE_SIN_HISTORIAL) == 0

    respuesta = cliente_historial_auto.post(
        URL_REGISTRO_MOVIL, json=DATOS_VINCULACION_SIN_HISTORIAL,
    )
    assert respuesta.status_code == 201

    paciente = db.get(Paciente, PACIENTE_SIN_HISTORIAL)
    assert paciente.usuario_id is not None

    historial = repo_historial.obtener_historial_por_paciente_id(
        db, PACIENTE_SIN_HISTORIAL,
    )
    assert historial is not None
    assert contar_historiales(db, PACIENTE_SIN_HISTORIAL) == 1
    assert sin_zona(historial.fecha_apertura) == sin_zona(paciente.fecha_registro)
    assert contar(db, HistorialClinico) == HISTORIALES_SEMBRADOS + 1
    assert acciones_bitacora(db) == ["VINCULAR_CUENTA_PACIENTE"]


def test_registro_movil_no_duplica_historial_existente(
    cliente_historial_auto, db_historial_auto,
):
    db = db_historial_auto

    respuesta = cliente_historial_auto.post(
        URL_REGISTRO_MOVIL, json=DATOS_VINCULACION_CON_HISTORIAL,
    )
    assert respuesta.status_code == 201

    paciente = db.get(Paciente, PACIENTE_CON_HISTORIAL)
    assert paciente.usuario_id is not None

    historial = repo_historial.obtener_historial_por_paciente_id(
        db, PACIENTE_CON_HISTORIAL,
    )
    assert historial.id == 10
    assert contar_historiales(db, PACIENTE_CON_HISTORIAL) == 1
    assert contar(db, HistorialClinico) == HISTORIALES_SEMBRADOS


def test_rollback_en_registro_movil_si_falla_el_historial(
    db_historial_auto, monkeypatch,
):
    db = db_historial_auto

    def explotar(*args, **kwargs):
        raise RuntimeError("fallo al asegurar el historial clínico")

    monkeypatch.setattr(
        repo_historial, "asegurar_historial_clinico_para_paciente", explotar,
    )

    with pytest.raises(RuntimeError):
        registro_paciente_service.registrar_cuenta_paciente(
            db, RegistroPacienteRequest(**DATOS_REGISTRO_NUEVO),
        )

    # Usuario, paciente, historial y bitácora: todo revertido.
    assert contar(db, Usuario) == USUARIOS_SEMBRADOS
    assert contar(db, Paciente) == PACIENTES_SEMBRADOS
    assert contar(db, HistorialClinico) == HISTORIALES_SEMBRADOS
    assert contar(db, Bitacora) == 0


# =========================================================
# OPERACIÓN IDEMPOTENTE DE ASEGURADO
# =========================================================


def test_asegurar_historial_es_idempotente(db_historial_auto):
    db = db_historial_auto
    paciente = db.get(Paciente, PACIENTE_SIN_HISTORIAL)

    primero = repo_historial.asegurar_historial_clinico_para_paciente(db, paciente)
    db.commit()
    segundo = repo_historial.asegurar_historial_clinico_para_paciente(db, paciente)
    db.commit()

    assert primero.id == segundo.id
    assert contar_historiales(db, PACIENTE_SIN_HISTORIAL) == 1
    assert contar(db, HistorialClinico) == HISTORIALES_SEMBRADOS + 1


def test_asegurar_historial_no_reactiva_un_historial_inactivo(db_historial_auto):
    db = db_historial_auto
    paciente = db.get(Paciente, PACIENTE_HISTORIAL_INACTIVO)

    historial = repo_historial.asegurar_historial_clinico_para_paciente(db, paciente)

    assert historial.id == 40
    assert historial.estado is False
    assert contar_historiales(db, PACIENTE_HISTORIAL_INACTIVO) == 1
    assert contar(db, HistorialClinico) == HISTORIALES_SEMBRADOS



