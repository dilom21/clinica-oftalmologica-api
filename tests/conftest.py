"""Fixtures locales: ninguna prueba puede conectarse a la BD compartida."""

from datetime import date, datetime, time, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import connection, session
from app.main import app
from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.gestion_historial_clinico.models.models import (
    AntecedenteClinico,
    HistorialClinico,
)
from app.modules.gestion_pacientes.models.models import Paciente


@pytest.fixture(autouse=True)
def bloquear_bd_compartida(monkeypatch):
    def rechazar_conexion(*args, **kwargs):
        pytest.fail("Las pruebas no deben conectarse a la base de datos compartida")

    monkeypatch.setattr(connection.engine, "connect", rechazar_conexion)


@pytest.fixture
def db_historial():
    # DDL exclusivo de SQLite en memoria. No se genera DDL desde Base ni
    # se ejecutan migraciones o instrucciones sobre PostgreSQL/Supabase.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
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
        "CREATE TABLE oftalmologo (id INTEGER PRIMARY KEY)",
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
    ]
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in tablas:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(
            "INSERT INTO rol VALUES (1, 'Administrador', NULL, 1, 0, '2026-01-01')"
        )
        conn.exec_driver_sql(
            "INSERT INTO usuario VALUES (7, 'prueba@example.test', 'sin-login', 1, '2026-01-01', 1)"
        )
        conn.exec_driver_sql("INSERT INTO oftalmologo VALUES (1)")
        conn.exec_driver_sql(
            "INSERT INTO accion VALUES (1, 'LECTURA', 1), (2, 'ESCRITURA', 1), (3, 'AMBAS', 1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO funcion VALUES (?, ?, 1)",
            [(13, "Consultar historial de citas"), (15, "Consultar historial clínico"),
             (16, "Gestionar antecedentes clínicos")],
        )
        conn.exec_driver_sql(
            "INSERT INTO rol_funcion VALUES (1, 1, 13, 3), (2, 1, 15, 3), (3, 1, 16, 3)"
        )

    with Session(engine, autoflush=False) as db:
        ahora = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        db.add_all([
            Paciente(id=1, nombres="Ana María", apellidos="Pérez", ci="CI-001",
                     fecha_registro=ahora, estado=True),
            Paciente(id=2, nombres="Ana", apellidos="López", ci="CI-002",
                     fecha_registro=ahora, estado=True),
            Paciente(id=3, nombres="Sin", apellidos="Historial", ci=None,
                     fecha_registro=ahora, estado=True),
            Paciente(id=4, nombres="Historial", apellidos="Inactivo", ci=None,
                     fecha_registro=ahora, estado=True),
            Paciente(id=5, nombres="Literal%_", apellidos="Prueba", ci=None,
                     fecha_registro=ahora, estado=True),
        ])
        db.flush()
        db.add_all([
            HistorialClinico(id=10, paciente_id=1, fecha_apertura=ahora, estado=True),
            HistorialClinico(id=20, paciente_id=2, fecha_apertura=ahora, estado=True),
            HistorialClinico(id=40, paciente_id=4, fecha_apertura=ahora, estado=False),
        ])
        db.flush()
        db.add_all([
            AntecedenteClinico(id=100, historial_clinico_id=10, tipo="ALERGIA",
                              descripcion="Antecedente inicial", fecha_registro=ahora, estado=True),
            AntecedenteClinico(id=101, historial_clinico_id=10, tipo="OTRO",
                              descripcion="Registro inactivo", fecha_registro=ahora, estado=False),
            AntecedenteClinico(id=102, historial_clinico_id=10, tipo="CIRUGIA",
                              descripcion="Segundo antecedente", fecha_registro=ahora, estado=True),
            AntecedenteClinico(id=400, historial_clinico_id=40, tipo="OTRO",
                              descripcion="Historial inactivo", fecha_registro=ahora, estado=True),
        ])
        for cita_id, paciente_id, dia, estado in [
            (1, 1, 1, "ATENDIDA"), (2, 1, 2, "CANCELADA"),
            (3, 1, 3, "PROGRAMADA"), (4, 2, 2, "ATENDIDA"),
        ]:
            db.add(Cita(
                id=cita_id, paciente_id=paciente_id, oftalmologo_id=1,
                fecha=date(2026, 9, dia), hora_inicio=time(8), hora_fin=time(9),
                estado=estado, canal=None, fecha_registro=ahora, fecha_actualizacion=ahora,
            ))
        db.commit()
        yield db
    engine.dispose()


@pytest.fixture
def cliente_historial(db_historial):
    def obtener_db_local():
        yield db_historial

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = obtener_db_local
    app.dependency_overrides[dependencies.get_db] = obtener_db_local
    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {crear_access_token(7, 1)}"
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)
