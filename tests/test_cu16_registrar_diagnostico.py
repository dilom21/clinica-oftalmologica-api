"""Pruebas unitarias CU16 - Registrar diagnóstico.

Fixture SQLite en memoria propio: no se conecta a la BD compartida ni genera
DDL desde los modelos SQLAlchemy.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_historial_clinico.models.models import ConsultaClinica, Diagnostico
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

DATOS_DIAGNOSTICO = {
    "nombre": "Miopía",
    "descripcion": "Miopía bilateral leve",
}


def _crear_esquema_cu16(engine):
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
            nombre VARCHAR(150) NOT NULL,
            descripcion TEXT,
            fecha_diagnostico TIMESTAMP NOT NULL,
            estado BOOLEAN NOT NULL
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
            """INSERT INTO funcion VALUES
                (15, 'Consultar historial clínico', 1),
                (17, 'Registrar consulta clínica', 1),
                (18, 'Registrar diagnóstico', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO rol_funcion VALUES
                (1, 1, 15, 1), (2, 1, 17, 2), (3, 1, 18, 2),
                (4, 2, 15, 1), (5, 2, 17, 2), (6, 2, 18, 2),
                (7, 3, 15, 1), (8, 3, 17, 2),
                (9, 4, 15, 1), (10, 4, 17, 2),
                (11, 5, 15, 1), (12, 5, 17, 2)"""
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
                (100, 10, 4, 1, '2026-09-03 08:00:00', 'Consulta previa', NULL, NULL, 1),
                (200, 20, NULL, 2, '2026-09-04 08:00:00', 'Otra consulta', NULL, NULL, 1),
                (300, 10, NULL, 1, '2026-09-05 08:00:00', 'Consulta inactiva', NULL, NULL, 0)"""
        )


@pytest.fixture
def db_cu16():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _crear_esquema_cu16(engine)

    with Session(engine, autoflush=False) as db:
        yield db

    engine.dispose()


@pytest.fixture
def cliente_cu16(db_cu16):
    def obtener_db_local():
        yield db_cu16

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


# =========================================================
# REGISTRO EXITOSO
# =========================================================


def test_post_registra_diagnostico_y_bitacora(cliente_cu16, db_cu16):
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu16.post(f"{URL}/100/diagnosticos", json=DATOS_DIAGNOSTICO)
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert set(datos) == {
        "id", "consulta_clinica_id", "nombre", "descripcion",
        "fecha_diagnostico", "estado",
    }
    assert datos["consulta_clinica_id"] == 100
    assert datos["nombre"] == "Miopía"
    assert datos["descripcion"] == "Miopía bilateral leve"
    assert datos["estado"] is True

    diagnostico = db_cu16.get(Diagnostico, datos["id"])
    assert diagnostico.consulta_clinica_id == 100
    assert diagnostico.nombre == "Miopía"
    assert diagnostico.descripcion == "Miopía bilateral leve"
    assert diagnostico.estado is True
    # SQLite no preserva timezone; comparar solo fecha
    assert diagnostico.fecha_diagnostico.date() == datetime.now(timezone.utc).date()

    bitacora = db_cu16.scalar(select(Bitacora))
    assert bitacora.usuario_id == 7
    assert bitacora.accion == "REGISTRAR_DIAGNOSTICO"
    assert bitacora.entidad_afectada == "diagnostico"
    assert bitacora.id_registro_afectado == diagnostico.id
    assert bitacora.descripcion == "Diagnóstico registrado"

    # Verificar que se puede leer el diagnóstico
    detalle = cliente_cu16.get(f"{URL}/100/diagnosticos").json()
    assert len(detalle) == 1
    assert detalle[0]["id"] == datos["id"]


def test_post_diagnostico_sin_descripcion(cliente_cu16, db_cu16):
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu16.post(f"{URL}/100/diagnosticos", json={"nombre": "Astigmatismo"})
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["nombre"] == "Astigmatismo"
    assert datos["descripcion"] is None
    assert datos["estado"] is True


def test_post_varios_diagnosticos_misma_consulta(cliente_cu16, db_cu16):
    """Una consulta puede tener múltiples diagnósticos."""
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
    cliente_cu16.post(f"{URL}/100/diagnosticos", json={"nombre": "Miopía"})
    cliente_cu16.post(f"{URL}/100/diagnosticos", json={"nombre": "Astigmatismo"})
    cliente_cu16.post(f"{URL}/100/diagnosticos", json={"nombre": "Presbicia"})

    diagnósticos = cliente_cu16.get(f"{URL}/100/diagnosticos").json()
    assert len(diagnósticos) == 3
    nombres = {d["nombre"] for d in diagnósticos}
    assert nombres == {"Miopía", "Astigmatismo", "Presbicia"}


# =========================================================
# SEGURIDAD Y ROLES
# =========================================================


@pytest.mark.parametrize("usuario", [
    USUARIO_ADMIN, USUARIO_RECEPCIONISTA, USUARIO_PACIENTE,
])
def test_solo_oftalmologo_puede_registrar(cliente_cu16, db_cu16, usuario):
    autenticar(cliente_cu16, usuario)
    respuesta = cliente_cu16.post(f"{URL}/100/diagnosticos", json=DATOS_DIAGNOSTICO)
    assert respuesta.status_code == 403
    # Puede fallar por rol o por permiso, ambos son 403
    assert contar(db_cu16, Diagnostico) == 0
    assert contar(db_cu16, Bitacora) == 0


def test_rol_sin_permiso_es_rechazado(cliente_cu16, db_cu16):
    autenticar(cliente_cu16, USUARIO_INVITADO)
    respuesta = cliente_cu16.post(f"{URL}/100/diagnosticos", json=DATOS_DIAGNOSTICO)
    assert respuesta.status_code == 403
    assert "No tiene permiso" in respuesta.json()["detail"]
    assert contar(db_cu16, Diagnostico) == 0


def test_oftalmologo_sin_perfil_activo(cliente_cu16, db_cu16):
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO_SIN_PERFIL)
    respuesta = cliente_cu16.post(f"{URL}/100/diagnosticos", json=DATOS_DIAGNOSTICO)
    assert respuesta.status_code == 403
    assert "perfil de oftalmólogo" in respuesta.json()["detail"]
    assert contar(db_cu16, Diagnostico) == 0
    assert contar(db_cu16, Bitacora) == 0


# =========================================================
# VALIDACIONES DE CONSULTA
# =========================================================


@pytest.mark.parametrize("consulta_id", [999, 300])
def test_consulta_inexistente_o_inactiva(cliente_cu16, db_cu16, consulta_id):
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu16.post(f"{URL}/{consulta_id}/diagnosticos", json=DATOS_DIAGNOSTICO)
    assert respuesta.status_code == 404
    assert "consulta" in respuesta.json()["detail"].lower()
    assert contar(db_cu16, Diagnostico) == 0
    assert contar(db_cu16, Bitacora) == 0


def test_consulta_de_otro_oftalmologo(cliente_cu16, db_cu16):
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)  # oftalmólogo 1
    respuesta = cliente_cu16.post(f"{URL}/200/diagnosticos", json=DATOS_DIAGNOSTICO)
    assert respuesta.status_code == 403
    assert "pertenece" in respuesta.json()["detail"].lower()
    assert contar(db_cu16, Diagnostico) == 0
    assert contar(db_cu16, Bitacora) == 0


# =========================================================
# VALIDACIÓN DE CAMPOS
# =========================================================


@pytest.mark.parametrize("cambios", [
    {"nombre": ""},
    {"nombre": "   "},
    {"nombre": "\t\n"},
    {"nombre": "X" * 151},
    {"oftalmologo_id": 8},
    {"consulta_clinica_id": 999},
    {"estado": True},
    {"fecha_diagnostico": "2026-01-01T00:00:00Z"},
    {"campo_desconocido": "x"},
])
def test_validacion_campos(cliente_cu16, db_cu16, cambios):
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu16.post(f"{URL}/100/diagnosticos", json={**DATOS_DIAGNOSTICO, **cambios})
    assert respuesta.status_code == 422
    assert contar(db_cu16, Diagnostico) == 0
    assert contar(db_cu16, Bitacora) == 0


# =========================================================
# TRANSACCIONALIDAD
# =========================================================


@pytest.mark.parametrize("fallo", ["bitacora", "commit", "flush", "refresh"])
def test_rollback_atomico(cliente_cu16, db_cu16, monkeypatch, fallo):
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
            patch.setattr(db_cu16, fallo, fallar)
        autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
        with pytest.raises(SQLAlchemyError):
            cliente_cu16.post(f"{URL}/100/diagnosticos", json=DATOS_DIAGNOSTICO)

    assert contar(db_cu16, Diagnostico) == 0
    assert contar(db_cu16, Bitacora) == 0


def test_un_solo_commit_cubre_diagnostico_y_bitacora(db_cu16, monkeypatch):
    """Diagnóstico + bitácora se confirman en un único commit."""
    from app.modules.gestion_historial_clinico.schemas.schemas import DiagnosticoCrear
    from app.modules.gestion_usuarios_seguridad.models.models import Usuario

    commit = MagicMock(wraps=db_cu16.commit)
    monkeypatch.setattr(db_cu16, "commit", commit)

    usuario = db_cu16.get(Usuario, 7)
    service.registrar_diagnostico(
        db_cu16,
        100,
        DiagnosticoCrear(nombre="Miopía"),
        usuario,
    )

    commit.assert_called_once()
    assert contar(db_cu16, Bitacora) == 1


def test_repositorio_sin_commit_y_fecha_utc():
    db = MagicMock()
    antes = datetime.now(timezone.utc)
    diagnostico = repo.crear_diagnostico(
        db,
        consulta_clinica_id=100,
        nombre="Miopía",
        descripcion="Test",
    )
    assert antes <= diagnostico.fecha_diagnostico <= datetime.now(timezone.utc)
    assert diagnostico.fecha_diagnostico.tzinfo == timezone.utc
    assert diagnostico.estado is True
    db.flush.assert_called_once()
    db.refresh.assert_called_once_with(diagnostico)
    db.commit.assert_not_called()


# =========================================================
# LISTADO (GET)
# =========================================================


def test_listar_diagnosticos_consulta_valida(cliente_cu16, db_cu16):
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
    cliente_cu16.post(f"{URL}/100/diagnosticos", json={"nombre": "Miopía"})
    cliente_cu16.post(f"{URL}/100/diagnosticos", json={"nombre": "Astigmatismo"})

    respuesta = cliente_cu16.get(f"{URL}/100/diagnosticos")
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert len(datos) == 2
    assert all(d["estado"] is True for d in datos)
    # Ordenados por fecha descendente
    assert datos[0]["fecha_diagnostico"] >= datos[1]["fecha_diagnostico"]


def test_listar_diagnosticos_consulta_inexistente(cliente_cu16):
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu16.get(f"{URL}/999/diagnosticos")
    assert respuesta.status_code == 404


def test_listar_diagnosticos_consulta_inactiva(cliente_cu16):
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu16.get(f"{URL}/300/diagnosticos")
    assert respuesta.status_code == 404


def test_listar_diagnosticos_sin_diagnosticos(cliente_cu16):
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu16.get(f"{URL}/100/diagnosticos")
    assert respuesta.status_code == 200
    assert respuesta.json() == []


# =========================================================
# REGRESIÓN CU15
# =========================================================


def test_cu15_sigue_funcionando(cliente_cu16, db_cu16):
    """CU15 no debe romperse por la implementación de CU16."""
    autenticar(cliente_cu16, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu16.post(URL, json={"historial_clinico_id": 10})
    assert respuesta.status_code == 201
    assert "id" in respuesta.json()
    assert respuesta.json()["estado"] is True