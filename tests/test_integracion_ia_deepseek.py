"""Pruebas unitarias de la integración IA con DeepSeek.

DeepSeek SIEMPRE se mockea: estas pruebas no llaman a Internet ni consumen
tokens ni requieren `DEEPSEEK_API_KEY`. La fixture SQLite es propia (no toca
la BD compartida) y crea localmente el permiso "Usar asistencia clínica IA".
"""

from datetime import datetime, timezone

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
)
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
from app.modules.integracion_ia.providers import deepseek_provider
from app.modules.integracion_ia.providers.deepseek_provider import (
    IAConfiguracionError,
    IAProveedorNoDisponibleError,
    IARespuestaInvalidaError,
)
from app.modules.integracion_ia.schemas import schemas
from app.modules.integracion_ia.services import service


URL_BASE = "/ia/consultas"

USUARIO_OFTALMOLOGO = (7, 1)
USUARIO_OFTALMOLOGO_2 = (12, 1)
USUARIO_ADMIN = (8, 2)
USUARIO_RECEPCIONISTA = (9, 3)
USUARIO_PACIENTE = (10, 4)
USUARIO_OFTALMOLOGO_SIN_PERFIL = (11, 1)
USUARIO_INVITADO = (13, 5)

ANALISIS_MODELO = {
    "resumen_clinico": "Paciente con molestia ocular leve.",
    "hallazgos_relevantes": ["Dolor ocular"],
    "aspectos_a_evaluar": ["Agudeza visual"],
    "hipotesis_orientativas": ["Posible irritación ocular"],
    "advertencia": "Texto del modelo que debe ser reemplazado por el backend.",
}

MEJORA_MODELO = {
    "descripcion_mejorada": "Paciente refiere visión borrosa de lejos en ambos ojos.",
}

DATOS_MEJORA = {
    "nombre": "Miopía",
    "descripcion": "paciente ve borroso de lejos ambos ojos",
}


def _crear_esquema_ia(engine):
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
            "INSERT INTO funcion VALUES (20, 'Usar asistencia clínica IA', 1)"
        )
        # Permiso LECTURA para Oftalmólogo (rol 1) y Administrador (rol 2).
        # El Administrador lo tiene a propósito: así el 403 proviene del
        # control de rol del service y no del permiso.
        conn.exec_driver_sql(
            "INSERT INTO rol_funcion VALUES (1, 1, 20, 1), (2, 2, 20, 1)"
        )
        conn.exec_driver_sql(
            """INSERT INTO paciente
                (id, usuario_id, nombres, apellidos, ci, fecha_registro, estado) VALUES
                (1, NULL, 'Ana', 'Pérez', 'CI-001', '2026-09-01', 1),
                (2, NULL, 'Luis', 'Gómez', 'CI-002', '2026-09-01', 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO oftalmologo
                (id, usuario_id, matricula, nombres, apellidos, especialidad, estado, fecha_registro) VALUES
                (1, 7, 'MAT-001', 'Salet', 'Ejemplo', 'Oftalmología General', 1, '2026-01-01'),
                (2, 12, 'MAT-002', 'Otro', 'Doctor', 'Retina', 1, '2026-01-01')"""
        )
        conn.exec_driver_sql(
            """INSERT INTO historial_clinico
                (id, paciente_id, fecha_apertura, observaciones_generales, estado) VALUES
                (10, 1, '2026-09-01', NULL, 1),
                (20, 2, '2026-09-01', NULL, 1)"""
        )
        conn.exec_driver_sql(
            """INSERT INTO consulta_clinica
                (id, historial_clinico_id, cita_id, oftalmologo_id, fecha_consulta,
                 motivo_consulta, anamnesis, observaciones, estado) VALUES
                (100, 10, NULL, 1, '2026-09-03 08:00:00',
                 'Dolor ocular', 'Paciente refiere molestia', 'Sin alergias', 1),
                (200, 20, NULL, 2, '2026-09-04 08:00:00',
                 'Otra consulta', NULL, NULL, 1),
                (300, 10, NULL, 1, '2026-09-05 08:00:00',
                 'Consulta inactiva', NULL, NULL, 0)"""
        )


@pytest.fixture
def db_ia():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _crear_esquema_ia(engine)

    with Session(engine, autoflush=False) as db:
        yield db

    engine.dispose()


@pytest.fixture
def cliente_ia(db_ia):
    def obtener_db_local():
        yield db_ia

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


def mock_provider(monkeypatch, resultado=None, error=None):
    """Reemplaza la llamada real a DeepSeek por un doble en memoria."""
    capturado = {}

    def fake_generar_json(system_prompt, user_prompt, **kwargs):
        capturado["system"] = system_prompt
        capturado["user"] = user_prompt
        if error is not None:
            raise error
        return resultado

    monkeypatch.setattr(
        service.deepseek_provider, "generar_json", fake_generar_json,
    )
    return capturado


# =========================================================
# ANÁLISIS - ÉXITO Y ESTRUCTURA
# =========================================================


def test_analizar_consulta_exitosa(cliente_ia, db_ia, monkeypatch):
    mock_provider(monkeypatch, ANALISIS_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(f"{URL_BASE}/100/analizar")

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert set(datos) == {
        "resumen_clinico", "hallazgos_relevantes", "aspectos_a_evaluar",
        "hipotesis_orientativas", "advertencia",
    }
    assert datos["resumen_clinico"] == ANALISIS_MODELO["resumen_clinico"]
    assert datos["hallazgos_relevantes"] == ["Dolor ocular"]
    assert datos["aspectos_a_evaluar"] == ["Agudeza visual"]
    assert datos["hipotesis_orientativas"] == ["Posible irritación ocular"]
    # No se crea ni modifica nada clínico.
    assert contar(db_ia, ConsultaClinica) == 3
    assert contar(db_ia, Diagnostico) == 0


def test_analisis_estructura_json_estable(cliente_ia, monkeypatch):
    mock_provider(monkeypatch, ANALISIS_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    datos = cliente_ia.post(f"{URL_BASE}/100/analizar").json()

    assert isinstance(datos["resumen_clinico"], str)
    assert isinstance(datos["hallazgos_relevantes"], list)
    assert isinstance(datos["aspectos_a_evaluar"], list)
    assert isinstance(datos["hipotesis_orientativas"], list)
    assert isinstance(datos["advertencia"], str)
    # La advertencia la impone el backend, no el modelo.
    assert datos["advertencia"] == schemas.ADVERTENCIA_ANALISIS


def test_analisis_registra_bitacora_sin_datos_sensibles(
    cliente_ia, db_ia, monkeypatch,
):
    mock_provider(monkeypatch, ANALISIS_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    cliente_ia.post(f"{URL_BASE}/100/analizar")

    assert contar(db_ia, Bitacora) == 1
    bitacora = db_ia.scalar(select(Bitacora))
    assert bitacora.usuario_id == 7
    assert bitacora.accion == "ANALIZAR_CONSULTA_IA"
    assert bitacora.entidad_afectada == "consulta_clinica"
    assert bitacora.id_registro_afectado == 100
    assert bitacora.descripcion == (
        "Análisis asistido por IA solicitado sobre consulta clínica"
    )
    # No guarda prompt, respuesta ni datos clínicos.
    assert "anamnesis" not in (bitacora.descripcion or "")
    assert "Paciente" not in (bitacora.descripcion or "")
    assert ANALISIS_MODELO["resumen_clinico"] not in (bitacora.descripcion or "")


def test_prompt_solo_contiene_campos_clinicos_permitidos(
    cliente_ia, monkeypatch,
):
    capturado = mock_provider(monkeypatch, ANALISIS_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    cliente_ia.post(f"{URL_BASE}/100/analizar")

    user = capturado["user"]
    assert "motivo_consulta" in user
    assert "anamnesis" in user
    assert "observaciones" in user
    assert "Dolor ocular" in user
    # El paciente 1 es 'Ana Pérez' / 'CI-001': no debe viajar PII.
    assert "Ana Pérez" not in user
    assert "Pérez" not in user
    assert "CI-001" not in user
    # El modelo tampoco recibe identificadores internos.
    assert "oftalmologo_id" not in user
    assert "paciente_id" not in user
    assert "historial_clinico_id" not in user


def test_analisis_no_modifica_la_consulta(cliente_ia, db_ia, monkeypatch):
    mock_provider(monkeypatch, ANALISIS_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    consulta = db_ia.get(ConsultaClinica, 100)
    antes = (
        consulta.motivo_consulta, consulta.anamnesis, consulta.observaciones,
        consulta.estado, consulta.oftalmologo_id,
    )

    assert cliente_ia.post(f"{URL_BASE}/100/analizar").status_code == 200

    db_ia.expire_all()
    consulta = db_ia.get(ConsultaClinica, 100)
    despues = (
        consulta.motivo_consulta, consulta.anamnesis, consulta.observaciones,
        consulta.estado, consulta.oftalmologo_id,
    )
    assert antes == despues


# =========================================================
# ANÁLISIS - VALIDACIONES Y ERRORES
# =========================================================


@pytest.mark.parametrize("consulta_id", [999, 300])
def test_analisis_consulta_inexistente_o_inactiva(
    cliente_ia, db_ia, monkeypatch, consulta_id,
):
    mock_provider(monkeypatch, ANALISIS_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(f"{URL_BASE}/{consulta_id}/analizar")

    assert respuesta.status_code == 404
    assert contar(db_ia, Bitacora) == 0


def test_analisis_consulta_de_otro_oftalmologo(cliente_ia, db_ia, monkeypatch):
    mock_provider(monkeypatch, ANALISIS_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(f"{URL_BASE}/200/analizar")

    assert respuesta.status_code == 403
    assert "pertenece" in respuesta.json()["detail"].lower()
    assert contar(db_ia, Bitacora) == 0


def test_analisis_oftalmologo_sin_perfil(cliente_ia, db_ia, monkeypatch):
    mock_provider(monkeypatch, ANALISIS_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO_SIN_PERFIL)

    respuesta = cliente_ia.post(f"{URL_BASE}/100/analizar")

    assert respuesta.status_code == 403
    assert "perfil de oftalmólogo" in respuesta.json()["detail"]
    assert contar(db_ia, Bitacora) == 0


@pytest.mark.parametrize("usuario", [USUARIO_ADMIN, USUARIO_RECEPCIONISTA])
def test_analisis_actor_no_oftalmologo(cliente_ia, db_ia, monkeypatch, usuario):
    mock_provider(monkeypatch, ANALISIS_MODELO)
    autenticar(cliente_ia, usuario)

    respuesta = cliente_ia.post(f"{URL_BASE}/100/analizar")

    assert respuesta.status_code == 403
    assert contar(db_ia, Bitacora) == 0


def test_analisis_rol_sin_permiso(cliente_ia, db_ia, monkeypatch):
    mock_provider(monkeypatch, ANALISIS_MODELO)
    autenticar(cliente_ia, USUARIO_INVITADO)

    respuesta = cliente_ia.post(f"{URL_BASE}/100/analizar")

    assert respuesta.status_code == 403
    assert "No tiene permiso" in respuesta.json()["detail"]
    assert contar(db_ia, Bitacora) == 0


def test_analisis_requiere_jwt(cliente_ia, db_ia):
    respuesta = cliente_ia.post(f"{URL_BASE}/100/analizar")

    assert respuesta.status_code in (401, 403)
    assert contar(db_ia, Bitacora) == 0


def test_analisis_no_llama_al_provider_si_no_esta_autorizado(
    cliente_ia, monkeypatch,
):
    capturado = mock_provider(
        monkeypatch, error=AssertionError("no debe llamarse al provider"),
    )
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(f"{URL_BASE}/200/analizar")

    assert respuesta.status_code == 403
    assert capturado == {}


# =========================================================
# ANÁLISIS - ERRORES DEL PROVEEDOR
# =========================================================


@pytest.mark.parametrize("respuesta_modelo", [
    {},
    {"resumen_clinico": 123},
    {"resumen_clinico": "   "},
    {"resumen_clinico": "ok", "hallazgos_relevantes": "no-es-lista"},
])
def test_analisis_respuesta_invalida_502(
    cliente_ia, db_ia, monkeypatch, respuesta_modelo,
):
    mock_provider(monkeypatch, respuesta_modelo)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(f"{URL_BASE}/100/analizar")

    assert respuesta.status_code == 502
    assert contar(db_ia, Bitacora) == 0


def test_analisis_provider_no_disponible_503(cliente_ia, db_ia, monkeypatch):
    mock_provider(monkeypatch, error=IAProveedorNoDisponibleError("caído"))
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(f"{URL_BASE}/100/analizar")

    assert respuesta.status_code == 503
    assert respuesta.json()["detail"] == "Servicio de IA no disponible"
    assert contar(db_ia, Bitacora) == 0


def test_analisis_provider_contenido_invalido_502(
    cliente_ia, db_ia, monkeypatch,
):
    mock_provider(monkeypatch, error=IARespuestaInvalidaError("no json"))
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(f"{URL_BASE}/100/analizar")

    assert respuesta.status_code == 502
    assert respuesta.json()["detail"] == "Respuesta inválida del servicio de IA"
    assert contar(db_ia, Bitacora) == 0


def test_analisis_sin_api_key_503(cliente_ia, db_ia, monkeypatch):
    """Sin DEEPSEEK_API_KEY el provider real falla antes de tocar la red."""
    monkeypatch.setattr(deepseek_provider, "DEEPSEEK_API_KEY", None)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(f"{URL_BASE}/100/analizar")

    assert respuesta.status_code == 503
    assert respuesta.json()["detail"] == "Servicio de IA no configurado"
    assert contar(db_ia, Bitacora) == 0


# =========================================================
# MEJORA DE REDACCIÓN - ÉXITO
# =========================================================


def test_mejorar_redaccion_exitosa(cliente_ia, db_ia, monkeypatch):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert set(datos) == {
        "nombre", "descripcion_original", "descripcion_mejorada", "advertencia",
    }
    assert datos["nombre"] == "Miopía"
    assert datos["descripcion_original"] == DATOS_MEJORA["descripcion"]
    assert datos["descripcion_mejorada"] == MEJORA_MODELO["descripcion_mejorada"]
    assert datos["advertencia"] == schemas.ADVERTENCIA_MEJORA


def test_mejorar_redaccion_conserva_el_nombre(cliente_ia, monkeypatch):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico",
        json={"nombre": "Astigmatismo", "descripcion": "ve distorsionado"},
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["nombre"] == "Astigmatismo"


def test_mejorar_redaccion_registra_bitacora(cliente_ia, db_ia, monkeypatch):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    assert contar(db_ia, Bitacora) == 1
    bitacora = db_ia.scalar(select(Bitacora))
    assert bitacora.usuario_id == 7
    assert bitacora.accion == "MEJORAR_REDACCION_DIAGNOSTICO_IA"
    assert bitacora.entidad_afectada == "consulta_clinica"
    assert bitacora.id_registro_afectado == 100
    assert bitacora.descripcion == (
        "Redacción diagnóstica asistida por IA generada"
    )
    # No guarda la descripción clínica.
    assert DATOS_MEJORA["descripcion"] not in (bitacora.descripcion or "")


def test_mejorar_redaccion_no_crea_ni_modifica_clinicos(
    cliente_ia, db_ia, monkeypatch,
):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    consulta = db_ia.get(ConsultaClinica, 100)
    antes = (consulta.motivo_consulta, consulta.anamnesis, consulta.observaciones)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    assert respuesta.status_code == 200
    assert contar(db_ia, Diagnostico) == 0
    db_ia.expire_all()
    consulta = db_ia.get(ConsultaClinica, 100)
    despues = (
        consulta.motivo_consulta, consulta.anamnesis, consulta.observaciones,
    )
    assert antes == despues


def test_mejorar_redaccion_prompt_sin_pii(cliente_ia, monkeypatch):
    capturado = mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    user = capturado["user"]
    assert "Miopía" in user
    assert DATOS_MEJORA["descripcion"] in user
    assert "Ana Pérez" not in user
    assert "CI-001" not in user


# =========================================================
# MEJORA DE REDACCIÓN - VALIDACIONES
# =========================================================


@pytest.mark.parametrize("body", [
    {"nombre": "Miopía"},
    {"nombre": "Miopía", "descripcion": ""},
    {"nombre": "Miopía", "descripcion": "   "},
    {"nombre": "Miopía", "descripcion": "\t\n"},
    {"nombre": "", "descripcion": "texto"},
    {"nombre": "   ", "descripcion": "texto"},
    {"descripcion": "texto"},
    {"nombre": "X" * 151, "descripcion": "texto"},
])
def test_mejorar_redaccion_body_invalido_422(
    cliente_ia, db_ia, monkeypatch, body,
):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=body,
    )

    assert respuesta.status_code == 422
    assert contar(db_ia, Bitacora) == 0


@pytest.mark.parametrize("campo_extra", [
    {"oftalmologo_id": 1},
    {"consulta_clinica_id": 100},
    {"diagnostico_id": 5},
    {"usuario_id": 7},
    {"estado": True},
    {"fecha_diagnostico": "2026-01-01T00:00:00Z"},
    {"campo_desconocido": "x"},
])
def test_mejorar_redaccion_rechaza_campos_extra_422(
    cliente_ia, db_ia, monkeypatch, campo_extra,
):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico",
        json={**DATOS_MEJORA, **campo_extra},
    )

    assert respuesta.status_code == 422
    assert contar(db_ia, Diagnostico) == 0
    assert contar(db_ia, Bitacora) == 0


@pytest.mark.parametrize("consulta_id", [999, 300])
def test_mejorar_redaccion_consulta_inexistente_o_inactiva(
    cliente_ia, db_ia, monkeypatch, consulta_id,
):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/{consulta_id}/mejorar-redaccion-diagnostico",
        json=DATOS_MEJORA,
    )

    assert respuesta.status_code == 404
    assert contar(db_ia, Bitacora) == 0


def test_mejorar_redaccion_consulta_ajena_403(cliente_ia, db_ia, monkeypatch):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/200/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    assert respuesta.status_code == 403
    assert contar(db_ia, Diagnostico) == 0
    assert contar(db_ia, Bitacora) == 0


def test_mejorar_redaccion_oftalmologo_sin_perfil(
    cliente_ia, db_ia, monkeypatch,
):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO_SIN_PERFIL)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    assert respuesta.status_code == 403
    assert contar(db_ia, Bitacora) == 0


@pytest.mark.parametrize("usuario", [USUARIO_ADMIN, USUARIO_PACIENTE])
def test_mejorar_redaccion_actor_no_oftalmologo(
    cliente_ia, db_ia, monkeypatch, usuario,
):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, usuario)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    assert respuesta.status_code == 403
    assert contar(db_ia, Bitacora) == 0


def test_mejorar_redaccion_rol_sin_permiso(cliente_ia, db_ia, monkeypatch):
    mock_provider(monkeypatch, MEJORA_MODELO)
    autenticar(cliente_ia, USUARIO_INVITADO)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    assert respuesta.status_code == 403
    assert "No tiene permiso" in respuesta.json()["detail"]
    assert contar(db_ia, Bitacora) == 0


def test_mejorar_redaccion_requiere_jwt(cliente_ia, db_ia):
    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    assert respuesta.status_code in (401, 403)
    assert contar(db_ia, Bitacora) == 0


# =========================================================
# MEJORA DE REDACCIÓN - ERRORES DEL PROVEEDOR
# =========================================================


def test_mejorar_redaccion_provider_no_disponible_503(
    cliente_ia, db_ia, monkeypatch,
):
    mock_provider(monkeypatch, error=IAProveedorNoDisponibleError("caído"))
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    assert respuesta.status_code == 503
    assert contar(db_ia, Diagnostico) == 0
    assert contar(db_ia, Bitacora) == 0


@pytest.mark.parametrize("respuesta_modelo", [
    {},
    {"descripcion_mejorada": 123},
    {"descripcion_mejorada": "   "},
])
def test_mejorar_redaccion_respuesta_invalida_502(
    cliente_ia, db_ia, monkeypatch, respuesta_modelo,
):
    mock_provider(monkeypatch, respuesta_modelo)
    autenticar(cliente_ia, USUARIO_OFTALMOLOGO)

    respuesta = cliente_ia.post(
        f"{URL_BASE}/100/mejorar-redaccion-diagnostico", json=DATOS_MEJORA,
    )

    assert respuesta.status_code == 502
    assert contar(db_ia, Diagnostico) == 0
    assert contar(db_ia, Bitacora) == 0


# =========================================================
# PROVIDER - SIN RED
# =========================================================


def test_provider_sin_api_key_lanza_config_error(monkeypatch):
    monkeypatch.setattr(deepseek_provider, "DEEPSEEK_API_KEY", None)

    with pytest.raises(IAConfiguracionError):
        deepseek_provider.generar_json("sistema", "usuario")


def test_provider_usa_parametros_json_y_max_tokens_por_defecto(monkeypatch):
    capturado = {}

    class Completions:
        def create(self, **kwargs):
            capturado.update(kwargs)
            return type(
                "Respuesta",
                (),
                {"choices": [type(
                    "Opcion",
                    (),
                    {"message": type(
                        "Mensaje", (), {"content": '{"ok": true}'},
                    )()},
                )()]},
            )()

    class Cliente:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(deepseek_provider, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(deepseek_provider, "OpenAI", Cliente)

    assert deepseek_provider.generar_json("sistema", "usuario") == {"ok": True}
    assert capturado["max_tokens"] == 2048
    assert capturado["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("contenido", ["no-json", "[]", "123", ""])
def test_provider_parsear_json_invalido(contenido):
    with pytest.raises(IARespuestaInvalidaError):
        deepseek_provider._parsear_json(contenido)


def test_provider_extrae_contenido_vacio():
    class RespuestaVacia:
        choices = []

    with pytest.raises(IARespuestaInvalidaError):
        deepseek_provider._extraer_contenido(RespuestaVacia())
