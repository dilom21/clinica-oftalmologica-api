"""Pruebas CU18: examenes oftalmologicos y sus resultados.

La base es SQLite en memoria. No se conecta a Supabase ni a Storage.
"""

from datetime import timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
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
    ExamenOftalmologico,
    ResultadoExamen,
    Tratamiento,
)
from app.modules.gestion_historial_clinico.repositories import repository as repo
from app.modules.gestion_historial_clinico.schemas.schemas import (
    ExamenOftalmologicoCrear,
    ResultadoExamenCrear,
)
from app.modules.gestion_historial_clinico.services import service
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora, Usuario


CONSULTA_PROPIA = 100
CONSULTA_AJENA = 200
CONSULTA_INACTIVA = 300
EXAMEN_PROPIO = 1000
EXAMEN_INACTIVO = 1001
EXAMEN_AJENO = 2000
EXAMEN_CONSULTA_INACTIVA = 3000

USUARIO_OFTALMOLOGO = (7, 1)
USUARIO_ADMIN = (8, 2)
USUARIO_RECEPCIONISTA = (9, 3)
USUARIO_PACIENTE = (10, 4)
USUARIO_SIN_PERFIL = (11, 1)

URL_CONSULTAS = "/historial-clinico/consultas"
URL_EXAMENES = "/historial-clinico/examenes"


def _ddl():
    return [
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
            id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL UNIQUE REFERENCES usuario(id),
            matricula TEXT NOT NULL UNIQUE, nombres TEXT NOT NULL, apellidos TEXT NOT NULL,
            especialidad TEXT, estado BOOLEAN NOT NULL, fecha_registro TIMESTAMP NOT NULL
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
            id INTEGER PRIMARY KEY, paciente_id INTEGER NOT NULL UNIQUE
                REFERENCES paciente(id) ON DELETE RESTRICT,
            fecha_apertura TIMESTAMP NOT NULL, observaciones_generales TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE antecedente_clinico (
            id INTEGER PRIMARY KEY, historial_clinico_id INTEGER NOT NULL
                REFERENCES historial_clinico(id) ON DELETE CASCADE,
            tipo VARCHAR(30) NOT NULL, descripcion TEXT NOT NULL,
            fecha_registro TIMESTAMP NOT NULL, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE bitacora (
            id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
            fecha_hora TIMESTAMP NOT NULL, ip TEXT, accion TEXT NOT NULL,
            entidad_afectada TEXT, id_registro_afectado INTEGER, descripcion TEXT
        )""",
        """CREATE TABLE consulta_clinica (
            id INTEGER PRIMARY KEY, historial_clinico_id INTEGER NOT NULL
                REFERENCES historial_clinico(id),
            cita_id INTEGER UNIQUE REFERENCES cita(id),
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
            fecha_consulta TIMESTAMP NOT NULL, motivo_consulta VARCHAR(255),
            anamnesis TEXT, observaciones TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE diagnostico (
            id INTEGER PRIMARY KEY, consulta_clinica_id INTEGER NOT NULL
                REFERENCES consulta_clinica(id) ON DELETE CASCADE,
            nombre VARCHAR(150) NOT NULL, descripcion TEXT,
            fecha_diagnostico TIMESTAMP NOT NULL, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE tratamiento (
            id INTEGER PRIMARY KEY, consulta_clinica_id INTEGER NOT NULL
                REFERENCES consulta_clinica(id) ON DELETE CASCADE,
            descripcion TEXT NOT NULL, observaciones TEXT,
            fecha_inicio DATE, fecha_fin DATE, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE indicacion (
            id INTEGER PRIMARY KEY, consulta_clinica_id INTEGER NOT NULL
                REFERENCES consulta_clinica(id) ON DELETE CASCADE,
            descripcion TEXT NOT NULL, fecha_registro TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE receta (
            id INTEGER PRIMARY KEY, consulta_clinica_id INTEGER NOT NULL
                REFERENCES consulta_clinica(id) ON DELETE CASCADE,
            fecha_emision TIMESTAMP NOT NULL, observaciones TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE detalle_receta (
            id INTEGER PRIMARY KEY, receta_id INTEGER NOT NULL
                REFERENCES receta(id) ON DELETE CASCADE,
            medicamento VARCHAR(150) NOT NULL, presentacion VARCHAR(100),
            dosis VARCHAR(100), frecuencia VARCHAR(100), duracion VARCHAR(100),
            indicaciones TEXT
        )""",
        """CREATE TABLE examen_oftalmologico (
            id INTEGER PRIMARY KEY, consulta_clinica_id INTEGER NOT NULL
                REFERENCES consulta_clinica(id) ON DELETE CASCADE,
            nombre_examen VARCHAR(150) NOT NULL,
            fecha_solicitud TIMESTAMP NOT NULL,
            observaciones TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE resultado_examen (
            id INTEGER PRIMARY KEY, examen_id INTEGER NOT NULL
                REFERENCES examen_oftalmologico(id) ON DELETE CASCADE,
            fecha_resultado TIMESTAMP NOT NULL, resultado TEXT NOT NULL,
            archivo_url TEXT, estado BOOLEAN NOT NULL
        )""",
    ]


def _crear_esquema(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in _ddl():
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
            (7, 'oftalmo@test', 'x', 1, '2026-01-01', 1),
            (8, 'admin@test', 'x', 1, '2026-01-01', 2),
            (9, 'recepcion@test', 'x', 1, '2026-01-01', 3),
            (10, 'paciente@test', 'x', 1, '2026-01-01', 4),
            (11, 'sinperfil@test', 'x', 1, '2026-01-01', 1),
            (12, 'oftalmo2@test', 'x', 1, '2026-01-01', 1)"""
        )
        conn.exec_driver_sql(
            "INSERT INTO accion VALUES (1, 'LECTURA', 1), (2, 'ESCRITURA', 1), (3, 'AMBAS', 1)"
        )
        conn.exec_driver_sql(
            """INSERT INTO funcion VALUES
            (15, 'Consultar historial clínico', 1),
            (17, 'Registrar consulta clínica', 1),
            (18, 'Registrar diagnóstico', 1),
            (19, 'Registrar tratamientos, indicaciones y recetas', 1),
            (20, 'Registrar resultados de exámenes oftalmológicos', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO rol_funcion VALUES
            (1, 1, 15, 1), (2, 1, 17, 2), (3, 1, 18, 2),
            (4, 1, 19, 2), (5, 1, 20, 2),
            (6, 2, 15, 3), (7, 2, 17, 3), (8, 2, 18, 3),
            (9, 2, 19, 3), (10, 2, 20, 3),
            (11, 3, 15, 1), (12, 3, 20, 1),
            (13, 4, 15, 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO paciente
            (id, nombres, apellidos, ci, fecha_registro, estado) VALUES
            (1, 'Ana', 'Pérez', 'CI-001', '2026-09-01', 1),
            (2, 'Luis', 'Gómez', 'CI-002', '2026-09-01', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO oftalmologo VALUES
            (1, 7, 'MAT-001', 'Salet', 'Ejemplo', 'General', 1, '2026-01-01'),
            (2, 12, 'MAT-002', 'Otro', 'Doctor', 'Retina', 1, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO cita
            (id, paciente_id, oftalmologo_id, fecha, hora_inicio, hora_fin,
             estado, fecha_registro, fecha_actualizacion) VALUES
            (4, 1, 1, '2026-09-03', '08:00', '08:30', 'ATENDIDA',
             '2026-09-03', '2026-09-03')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO historial_clinico VALUES
            (10, 1, '2026-09-01', NULL, 1),
            (20, 2, '2026-09-01', NULL, 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO antecedente_clinico VALUES
            (50, 10, 'OTRO', 'Antecedente', '2026-09-01', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO consulta_clinica VALUES
            (100, 10, 4, 1, '2026-09-03 08:00', 'Control', NULL, NULL, 1),
            (200, 20, NULL, 2, '2026-09-04 08:00', 'Retina', NULL, NULL, 1),
            (300, 10, NULL, 1, '2026-09-05 08:00', 'Inactiva', NULL, NULL, 0)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO diagnostico VALUES
            (1, 100, 'Miopía', NULL, '2026-09-03 09:00', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO tratamiento VALUES
            (1, 100, 'Lentes', NULL, NULL, NULL, 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO examen_oftalmologico VALUES
            (1000, 100, 'Tonometría', '2026-09-03 10:00', NULL, 1),
            (1001, 100, 'Examen inactivo', '2026-09-03 11:00', NULL, 0),
            (1002, 100, 'Agudeza visual', '2026-09-04 10:00', 'Sin lentes', 1),
            (2000, 200, 'Fondo de ojo', '2026-09-05 10:00', NULL, 1),
            (3000, 300, 'Consulta inactiva', '2026-09-06 10:00', NULL, 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO resultado_examen VALUES
            (5001, 1000, '2026-09-03 10:30', 'OD 16; OI 15', NULL, 1),
            (5002, 1000, '2026-09-03 11:30', 'Control OD 15; OI 15', 'https://example.test/control.png', 1),
            (5003, 1000, '2026-09-03 12:30', 'Resultado inactivo', NULL, 0),
            (5004, 1001, '2026-09-03 11:30', 'Examen inactivo', NULL, 1)"""
        )


@pytest.fixture
def db_cu18():
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
def cliente_cu18(db_cu18):
    def obtener_db_local():
        yield db_cu18

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


def test_oftalmologo_registra_examen_en_consulta_propia(cliente_cu18, db_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.post(
        f"{URL_CONSULTAS}/{CONSULTA_PROPIA}/examenes",
        json={"nombre_examen": "  OCT macular  ", "observaciones": "  Control  "},
    )
    assert respuesta.status_code == 201
    assert respuesta.json()["nombre_examen"] == "OCT macular"
    assert respuesta.json()["observaciones"] == "Control"
    assert respuesta.json()["consulta_clinica_id"] == CONSULTA_PROPIA
    assert respuesta.json()["estado"] is True
    assert respuesta.json()["fecha_solicitud"]
    bitacora = db_cu18.scalar(
        select(Bitacora).where(
            Bitacora.accion == "REGISTRAR_EXAMEN_OFTALMOLOGICO"
        )
    )
    assert bitacora.entidad_afectada == "examen_oftalmologico"
    assert bitacora.id_registro_afectado == respuesta.json()["id"]


def test_administrador_registra_examen_en_cualquier_consulta(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_ADMIN)
    respuesta = cliente_cu18.post(
        f"{URL_CONSULTAS}/{CONSULTA_AJENA}/examenes",
        json={"nombre_examen": "Campimetría"},
    )
    assert respuesta.status_code == 201
    assert respuesta.json()["consulta_clinica_id"] == CONSULTA_AJENA


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"nombre_examen": ""},
        {"nombre_examen": "   "},
        {"nombre_examen": "x" * 151},
        {"nombre_examen": "OCT", "id": 3},
        {"nombre_examen": "OCT", "consulta_clinica_id": 100},
        {"nombre_examen": "OCT", "fecha_solicitud": "2026-01-01"},
        {"nombre_examen": "OCT", "estado": False},
        {"nombre_examen": "OCT", "oftalmologo_id": 1},
        {"nombre_examen": "OCT", "usuario_id": 7},
        {"nombre_examen": "OCT", "paciente_id": 1},
    ],
)
def test_validaciones_examen_422(cliente_cu18, cuerpo):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.post(
        f"{URL_CONSULTAS}/{CONSULTA_PROPIA}/examenes", json=cuerpo,
    )
    assert respuesta.status_code == 422


def test_observaciones_examen_opcionales_y_vacias_a_none(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    for cuerpo in (
        {"nombre_examen": "Biomicroscopía"},
        {"nombre_examen": "Paquimetría", "observaciones": "   "},
    ):
        respuesta = cliente_cu18.post(
            f"{URL_CONSULTAS}/{CONSULTA_PROPIA}/examenes", json=cuerpo,
        )
        assert respuesta.status_code == 201
        assert respuesta.json()["observaciones"] is None


@pytest.mark.parametrize(
    "consulta_id, esperado",
    [(999, 404), (CONSULTA_INACTIVA, 404), (CONSULTA_AJENA, 403)],
)
def test_validacion_consulta_para_examen(cliente_cu18, consulta_id, esperado):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.post(
        f"{URL_CONSULTAS}/{consulta_id}/examenes",
        json={"nombre_examen": "OCT"},
    )
    assert respuesta.status_code == esperado


def test_oftalmologo_sin_perfil_no_registra_examen(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_SIN_PERFIL)
    respuesta = cliente_cu18.post(
        f"{URL_CONSULTAS}/{CONSULTA_PROPIA}/examenes",
        json={"nombre_examen": "OCT"},
    )
    assert respuesta.status_code == 403


@pytest.mark.parametrize("usuario", [USUARIO_PACIENTE, USUARIO_RECEPCIONISTA])
def test_paciente_y_recepcionista_no_registran_examen(cliente_cu18, usuario):
    autenticar(cliente_cu18, usuario)
    respuesta = cliente_cu18.post(
        f"{URL_CONSULTAS}/{CONSULTA_PROPIA}/examenes",
        json={"nombre_examen": "OCT"},
    )
    assert respuesta.status_code == 403


@pytest.mark.parametrize("usuario_id", [9, 10])
def test_service_es_autoridad_final_para_roles_no_admitidos(db_cu18, usuario_id):
    usuario = db_cu18.get(Usuario, usuario_id)
    with pytest.raises(HTTPException) as exc:
        service.registrar_examen(
            db_cu18,
            CONSULTA_PROPIA,
            ExamenOftalmologicoCrear(nombre_examen="OCT"),
            usuario,
        )
    assert exc.value.status_code == 403


def test_examen_y_bitacora_comparten_transaccion(db_cu18, monkeypatch):
    usuario = db_cu18.get(Usuario, 7)
    commit = MagicMock()
    monkeypatch.setattr(db_cu18, "commit", commit)
    service.registrar_examen(
        db_cu18,
        CONSULTA_PROPIA,
        ExamenOftalmologicoCrear(nombre_examen="Topografía"),
        usuario,
    )
    assert commit.call_count == 1
    assert not db_cu18.new
    db_cu18.rollback()


@pytest.mark.parametrize("fallo", ["bitacora", "commit"])
def test_examen_rollback_si_falla(cliente_cu18, db_cu18, monkeypatch, fallo):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    antes = contar(db_cu18, ExamenOftalmologico)
    if fallo == "bitacora":
        monkeypatch.setattr(
            service, "registrar_bitacora", MagicMock(side_effect=RuntimeError("fallo")),
        )
    else:
        monkeypatch.setattr(
            db_cu18, "commit", MagicMock(side_effect=RuntimeError("fallo")),
        )
    with pytest.raises(RuntimeError):
        cliente_cu18.post(
            f"{URL_CONSULTAS}/{CONSULTA_PROPIA}/examenes",
            json={"nombre_examen": "OCT"},
        )
    assert contar(db_cu18, ExamenOftalmologico) == antes


def test_repositorio_examen_no_hace_commit():
    db = MagicMock()
    db.refresh.side_effect = lambda objeto: setattr(objeto, "id", 99)
    examen = repo.crear_examen(
        db,
        consulta_clinica_id=100,
        nombre_examen="OCT",
        observaciones=None,
    )
    db.add.assert_called_once_with(examen)
    db.flush.assert_called_once()
    db.commit.assert_not_called()
    assert examen.fecha_solicitud.tzinfo is timezone.utc


def test_listar_examenes_activos_ordenados_con_resultados(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.get(
        f"{URL_CONSULTAS}/{CONSULTA_PROPIA}/examenes"
    )
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert [item["id"] for item in datos] == [1002, 1000]
    assert [item["id"] for item in datos[1]["resultados"]] == [5002, 5001]
    assert all(item["estado"] for item in datos)
    assert all(r["estado"] for item in datos for r in item["resultados"])


@pytest.mark.parametrize("consulta_id", [999, CONSULTA_INACTIVA])
def test_listar_examenes_valida_consulta(cliente_cu18, consulta_id):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.get(f"{URL_CONSULTAS}/{consulta_id}/examenes")
    assert respuesta.status_code == 404


def test_registrar_resultado_valido_con_archivo_null(cliente_cu18, db_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.post(
        f"{URL_EXAMENES}/{EXAMEN_PROPIO}/resultados",
        json={"resultado": "  OD: 16 mmHg. OI: 15 mmHg.  ", "archivo_url": None},
    )
    assert respuesta.status_code == 201
    assert respuesta.json()["resultado"] == "OD: 16 mmHg. OI: 15 mmHg."
    assert respuesta.json()["archivo_url"] is None
    bitacora = db_cu18.scalar(
        select(Bitacora).where(Bitacora.accion == "REGISTRAR_RESULTADO_EXAMEN")
    )
    assert bitacora.entidad_afectada == "resultado_examen"
    assert bitacora.id_registro_afectado == respuesta.json()["id"]


@pytest.mark.parametrize("archivo", [None, "", "   ", "https://cdn.test/oct.png"])
def test_archivo_url_opcional_y_se_conserva(cliente_cu18, archivo):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    cuerpo = {"resultado": "Resultado válido"}
    if archivo is not None:
        cuerpo["archivo_url"] = archivo
    respuesta = cliente_cu18.post(
        f"{URL_EXAMENES}/{EXAMEN_PROPIO}/resultados", json=cuerpo,
    )
    assert respuesta.status_code == 201
    esperado = archivo.strip() or None if isinstance(archivo, str) else None
    assert respuesta.json()["archivo_url"] == esperado


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"resultado": ""},
        {"resultado": "   "},
        {"resultado": "ok", "id": 1},
        {"resultado": "ok", "examen_id": 1000},
        {"resultado": "ok", "fecha_resultado": "2026-01-01"},
        {"resultado": "ok", "estado": False},
        {"resultado": "ok", "usuario_id": 7},
        {"resultado": "ok", "oftalmologo_id": 1},
    ],
)
def test_validaciones_resultado_422(cliente_cu18, cuerpo):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.post(
        f"{URL_EXAMENES}/{EXAMEN_PROPIO}/resultados", json=cuerpo,
    )
    assert respuesta.status_code == 422


@pytest.mark.parametrize(
    "examen_id, esperado",
    [
        (9999, 404),
        (EXAMEN_INACTIVO, 404),
        (EXAMEN_CONSULTA_INACTIVA, 404),
        (EXAMEN_AJENO, 403),
    ],
)
def test_validacion_examen_para_resultado(cliente_cu18, examen_id, esperado):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.post(
        f"{URL_EXAMENES}/{examen_id}/resultados",
        json={"resultado": "Normal"},
    )
    assert respuesta.status_code == esperado


def test_admin_registra_resultado_en_examen_ajeno(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_ADMIN)
    respuesta = cliente_cu18.post(
        f"{URL_EXAMENES}/{EXAMEN_AJENO}/resultados",
        json={"resultado": "Sin alteraciones"},
    )
    assert respuesta.status_code == 201


@pytest.mark.parametrize("usuario", [USUARIO_PACIENTE, USUARIO_RECEPCIONISTA])
def test_paciente_y_recepcionista_no_registran_resultado(cliente_cu18, usuario):
    autenticar(cliente_cu18, usuario)
    respuesta = cliente_cu18.post(
        f"{URL_EXAMENES}/{EXAMEN_PROPIO}/resultados",
        json={"resultado": "Normal"},
    )
    assert respuesta.status_code == 403


def test_multiples_resultados_en_mismo_examen(cliente_cu18, db_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    antes = db_cu18.scalar(
        select(func.count()).select_from(ResultadoExamen).where(
            ResultadoExamen.examen_id == EXAMEN_PROPIO
        )
    )
    for texto in ("Resultado inicial", "Resultado de control"):
        assert cliente_cu18.post(
            f"{URL_EXAMENES}/{EXAMEN_PROPIO}/resultados",
            json={"resultado": texto},
        ).status_code == 201
    despues = db_cu18.scalar(
        select(func.count()).select_from(ResultadoExamen).where(
            ResultadoExamen.examen_id == EXAMEN_PROPIO
        )
    )
    assert despues == antes + 2


@pytest.mark.parametrize("fallo", ["bitacora", "commit"])
def test_resultado_rollback_si_falla(cliente_cu18, db_cu18, monkeypatch, fallo):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    antes = contar(db_cu18, ResultadoExamen)
    if fallo == "bitacora":
        monkeypatch.setattr(
            service, "registrar_bitacora", MagicMock(side_effect=RuntimeError("fallo")),
        )
    else:
        monkeypatch.setattr(
            db_cu18, "commit", MagicMock(side_effect=RuntimeError("fallo")),
        )
    with pytest.raises(RuntimeError):
        cliente_cu18.post(
            f"{URL_EXAMENES}/{EXAMEN_PROPIO}/resultados",
            json={"resultado": "No persistir"},
        )
    assert contar(db_cu18, ResultadoExamen) == antes


def test_repositorio_resultado_no_hace_commit():
    db = MagicMock()
    db.refresh.side_effect = lambda objeto: setattr(objeto, "id", 99)
    resultado = repo.crear_resultado_examen(
        db,
        examen_id=1000,
        resultado="Normal",
        archivo_url=None,
    )
    db.add.assert_called_once_with(resultado)
    db.flush.assert_called_once()
    db.commit.assert_not_called()
    assert resultado.fecha_resultado.tzinfo is timezone.utc


def test_listar_resultados_solo_activos_y_ordenados(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.get(
        f"{URL_EXAMENES}/{EXAMEN_PROPIO}/resultados"
    )
    assert respuesta.status_code == 200
    assert [item["id"] for item in respuesta.json()] == [5002, 5001]


@pytest.mark.parametrize(
    "examen_id", [9999, EXAMEN_INACTIVO, EXAMEN_CONSULTA_INACTIVA]
)
def test_listar_resultados_valida_examen(cliente_cu18, examen_id):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.get(f"{URL_EXAMENES}/{examen_id}/resultados")
    assert respuesta.status_code == 404


def test_get_examen_incluye_resultados_anidados(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.get(f"{URL_EXAMENES}/{EXAMEN_PROPIO}")
    assert respuesta.status_code == 200
    assert respuesta.json()["id"] == EXAMEN_PROPIO
    assert [r["id"] for r in respuesta.json()["resultados"]] == [5002, 5001]


@pytest.mark.parametrize("examen_id", [9999, EXAMEN_INACTIVO])
def test_get_examen_inexistente_o_inactivo(cliente_cu18, examen_id):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    assert cliente_cu18.get(f"{URL_EXAMENES}/{examen_id}").status_code == 404


def test_cu18_no_contiene_integracion_storage():
    import inspect

    codigo = "\n".join(
        inspect.getsource(modulo)
        for modulo in (service, repo)
    ).lower()
    assert "supabase storage" not in codigo
    assert "upload(" not in codigo
    assert "multipart" not in codigo


def test_cu13_sigue_funcionando(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.get("/historial-clinico/1")
    assert respuesta.status_code == 200
    assert respuesta.json()["historial"]["id"] == 10


def test_cu15_sigue_funcionando(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.get(f"{URL_CONSULTAS}/{CONSULTA_PROPIA}")
    assert respuesta.status_code == 200
    assert respuesta.json()["id"] == CONSULTA_PROPIA


def test_cu16_sigue_funcionando(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.get(
        f"{URL_CONSULTAS}/{CONSULTA_PROPIA}/diagnosticos"
    )
    assert respuesta.status_code == 200
    assert respuesta.json()[0]["nombre"] == "Miopía"


def test_cu17_sigue_funcionando(cliente_cu18):
    autenticar(cliente_cu18, USUARIO_OFTALMOLOGO)
    respuesta = cliente_cu18.get(
        f"{URL_CONSULTAS}/{CONSULTA_PROPIA}/tratamientos"
    )
    assert respuesta.status_code == 200
    assert respuesta.json()[0]["descripcion"] == "Lentes"


def test_modelo_resultado_no_impone_unicidad_al_examen():
    columna = ResultadoExamen.__table__.c.examen_id
    assert columna.unique is not True
    assert not any(
        getattr(restriccion, "columns", None)
        and list(restriccion.columns.keys()) == ["examen_id"]
        and restriccion.__class__.__name__ == "UniqueConstraint"
        for restriccion in ResultadoExamen.__table__.constraints
    )


def test_schemas_rechazan_campos_controlados_directamente():
    with pytest.raises(Exception):
        ExamenOftalmologicoCrear(
            nombre_examen="OCT", consulta_clinica_id=100,
        )
    with pytest.raises(Exception):
        ResultadoExamenCrear(resultado="Normal", examen_id=1000)


def test_rutas_cu18_sin_colisiones_en_openapi():
    rutas = app.openapi()["paths"]
    esperadas = {
        "/historial-clinico/consultas/{consulta_id}/examenes": {"get", "post"},
        "/historial-clinico/examenes/{examen_id}/resultados": {"get", "post"},
        "/historial-clinico/examenes/{examen_id}": {"get"},
    }
    for ruta, metodos in esperadas.items():
        assert ruta in rutas
        assert metodos.issubset(rutas[ruta])
