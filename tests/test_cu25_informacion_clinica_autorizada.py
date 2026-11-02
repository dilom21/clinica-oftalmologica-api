"""CU25: información clínica propia con autenticación tenant real y aislada."""

from datetime import date, datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.security import (
    crear_access_token,
    crear_saas_access_token,
    crear_tenant_access_token,
)
from app.core.tenancy.dependencies import get_tenant_engine_registry
from app.core.tenancy.models import Empresa, PlanSaas, Suscripcion, TenantDatabase
from app.database import session as clinical_session
from app.main import app


URL = "/historial-clinico/mi-informacion"


SCHEMA = """
CREATE TABLE rol (
    id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT,
    estado BOOLEAN NOT NULL, protegido BOOLEAN NOT NULL,
    fecha_creacion TIMESTAMP NOT NULL
);
CREATE TABLE usuario (
    id INTEGER PRIMARY KEY, correo TEXT NOT NULL, password_hash TEXT NOT NULL,
    estado BOOLEAN NOT NULL, fecha_creacion TIMESTAMP NOT NULL,
    rol_id INTEGER NOT NULL REFERENCES rol(id)
);
CREATE TABLE paciente (
    id INTEGER PRIMARY KEY, usuario_id INTEGER UNIQUE REFERENCES usuario(id),
    nombres TEXT NOT NULL, apellidos TEXT NOT NULL, ci TEXT,
    fecha_nacimiento DATE, sexo TEXT, telefono TEXT, contacto_emergencia TEXT,
    fecha_registro TIMESTAMP NOT NULL, direccion TEXT, estado BOOLEAN NOT NULL
);
CREATE TABLE oftalmologo (
    id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL UNIQUE REFERENCES usuario(id),
    matricula TEXT NOT NULL UNIQUE, nombres TEXT NOT NULL, apellidos TEXT NOT NULL,
    especialidad TEXT, estado BOOLEAN NOT NULL, fecha_registro TIMESTAMP NOT NULL
);
CREATE TABLE historial_clinico (
    id INTEGER PRIMARY KEY, paciente_id INTEGER NOT NULL UNIQUE REFERENCES paciente(id),
    fecha_apertura TIMESTAMP NOT NULL, observaciones_generales TEXT,
    estado BOOLEAN NOT NULL
);
CREATE TABLE antecedente_clinico (
    id INTEGER PRIMARY KEY,
    historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id),
    tipo VARCHAR(30) NOT NULL, descripcion TEXT NOT NULL,
    fecha_registro TIMESTAMP NOT NULL, estado BOOLEAN NOT NULL
);
CREATE TABLE consulta_clinica (
    id INTEGER PRIMARY KEY,
    historial_clinico_id INTEGER NOT NULL REFERENCES historial_clinico(id),
    cita_id INTEGER UNIQUE, oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
    fecha_consulta TIMESTAMP NOT NULL, motivo_consulta VARCHAR(255),
    anamnesis TEXT, observaciones TEXT, estado BOOLEAN NOT NULL
);
CREATE TABLE diagnostico (
    id INTEGER PRIMARY KEY,
    consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
    nombre VARCHAR(150) NOT NULL, descripcion TEXT,
    fecha_diagnostico TIMESTAMP NOT NULL, estado BOOLEAN NOT NULL
);
CREATE TABLE tratamiento (
    id INTEGER PRIMARY KEY,
    consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
    descripcion TEXT NOT NULL, observaciones TEXT, fecha_inicio DATE,
    fecha_fin DATE, estado BOOLEAN NOT NULL
);
CREATE TABLE indicacion (
    id INTEGER PRIMARY KEY,
    consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
    descripcion TEXT NOT NULL, fecha_registro TIMESTAMP NOT NULL
);
CREATE TABLE receta (
    id INTEGER PRIMARY KEY,
    consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
    fecha_emision TIMESTAMP NOT NULL, observaciones TEXT, estado BOOLEAN NOT NULL
);
CREATE TABLE detalle_receta (
    id INTEGER PRIMARY KEY, receta_id INTEGER NOT NULL REFERENCES receta(id),
    medicamento VARCHAR(150) NOT NULL, presentacion VARCHAR(100), dosis VARCHAR(100),
    frecuencia VARCHAR(100), duracion VARCHAR(100), indicaciones TEXT
);
CREATE TABLE examen_oftalmologico (
    id INTEGER PRIMARY KEY,
    consulta_clinica_id INTEGER NOT NULL REFERENCES consulta_clinica(id),
    nombre_examen VARCHAR(150) NOT NULL, fecha_solicitud TIMESTAMP NOT NULL,
    observaciones TEXT, estado BOOLEAN NOT NULL
);
CREATE TABLE resultado_examen (
    id INTEGER PRIMARY KEY,
    examen_id INTEGER NOT NULL REFERENCES examen_oftalmologico(id),
    fecha_resultado TIMESTAMP NOT NULL, resultado TEXT NOT NULL,
    archivo_url TEXT, estado BOOLEAN NOT NULL
);
CREATE TABLE control_medico (
    id INTEGER PRIMARY KEY, consulta_clinica_id INTEGER REFERENCES consulta_clinica(id),
    paciente_id INTEGER NOT NULL REFERENCES paciente(id),
    oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id),
    fecha_programada DATE NOT NULL, motivo VARCHAR(255), observaciones TEXT,
    estado VARCHAR(30) DEFAULT 'PROGRAMADO'
);
"""


USUARIOS = """
INSERT INTO rol VALUES
    (1, 'Oftalmólogo', NULL, 1, 0, '2026-01-01'),
    (3, 'Recepcionista', NULL, 1, 0, '2026-01-01'),
    (4, 'Paciente', NULL, 1, 0, '2026-01-01'),
    (5, 'Paciente', NULL, 0, 0, '2026-01-01');
INSERT INTO usuario VALUES
    (7, 'oftalmo@test', 'x', 1, '2026-01-01', 1),
    (9, 'recepcion@test', 'x', 1, '2026-01-01', 3),
    (10, 'ana@test', 'x', 1, '2026-01-01', 4),
    (14, 'luis@test', 'x', 1, '2026-01-01', 4),
    (15, 'inactivo@test', 'x', 1, '2026-01-01', 4),
    (16, 'sinperfil@test', 'x', 1, '2026-01-01', 4),
    (17, 'usuarioinactivo@test', 'x', 0, '2026-01-01', 4),
    (18, 'rolinactivo@test', 'x', 1, '2026-01-01', 5);
INSERT INTO paciente
    (id, usuario_id, nombres, apellidos, ci, fecha_registro, estado) VALUES
    (1, 10, 'Ana', 'Pérez', 'CI-001', '2026-09-01', 1),
    (2, 14, 'Luis', 'Gómez', 'CI-002', '2026-09-01', 1),
    (5, 15, 'Inés', 'Quiroga', 'CI-005', '2026-09-01', 0),
    (7, 17, 'Usuario', 'Inactivo', 'CI-007', '2026-09-01', 1),
    (8, 18, 'Rol', 'Inactivo', 'CI-008', '2026-09-01', 1);
INSERT INTO oftalmologo VALUES
    (1, 7, 'MAT-001', 'Salet', 'Ejemplo', 'Glaucoma', 1, '2026-01-01');
"""


ALPHA_DATA = """
INSERT INTO historial_clinico VALUES
    (10, 1, '2026-09-01 10:00:00', 'ALPHA: usa lentes desde 2018', 1),
    (20, 2, '2026-09-01 10:00:00', 'Historial de otro paciente', 1),
    (50, 5, '2026-09-01 10:00:00', 'Paciente inactivo', 1);
INSERT INTO antecedente_clinico VALUES
    (1, 10, 'ALERGIA', 'Penicilina', '2026-09-01 10:00:00', 1),
    (2, 10, 'ENFERMEDAD', 'Antecedente retirado', '2026-09-02 10:00:00', 0),
    (3, 20, 'OTRO', 'Antecedente ajeno', '2026-09-01 10:00:00', 1);
INSERT INTO consulta_clinica VALUES
    (100, 10, NULL, 1, '2026-10-01 15:00:00', 'Control ALPHA',
     'Presión ocular elevada', 'Continuar tratamiento', 1),
    (101, 10, NULL, 1, '2026-08-01 15:00:00', 'Consulta no autorizada',
     NULL, NULL, 0),
    (200, 20, NULL, 1, '2026-10-02 15:00:00', 'Consulta de otro paciente',
     NULL, NULL, 1);
INSERT INTO diagnostico VALUES
    (1, 100, 'Glaucoma', 'Ojo derecho', '2026-10-01 16:00:00', 1),
    (2, 100, 'Diagnóstico retirado', NULL, '2026-10-01 16:05:00', 0),
    (3, 200, 'Miopía ajena', NULL, '2026-10-02 16:00:00', 1);
INSERT INTO tratamiento VALUES
    (1, 100, 'Timolol 0.5%', 'Una gota', '2026-10-01', '2026-11-01', 1),
    (2, 100, 'Tratamiento retirado', NULL, NULL, NULL, 0);
INSERT INTO indicacion VALUES
    (1, 100, 'No frotar los ojos', '2026-10-01 16:10:00');
INSERT INTO receta VALUES
    (1, 100, '2026-10-01 16:20:00', 'Receta vigente', 1),
    (2, 100, '2026-09-01 16:20:00', 'Receta anulada', 0);
INSERT INTO detalle_receta VALUES
    (1, 1, 'Timolol', 'Colirio', '0.5%', 'cada 12 horas', '30 días', 'Ojo derecho'),
    (2, 2, 'Medicamento anulado', NULL, NULL, NULL, NULL, NULL);
INSERT INTO examen_oftalmologico VALUES
    (1, 100, 'Tonometría', '2026-10-01 15:30:00', NULL, 1),
    (2, 100, 'Examen anulado', '2026-10-01 15:40:00', NULL, 0);
INSERT INTO resultado_examen VALUES
    (1, 1, '2026-10-01 17:00:00', 'PIO 22 mmHg', 'interno/privado.pdf', 1),
    (2, 1, '2026-10-01 17:05:00', 'Resultado retirado', NULL, 0);
INSERT INTO control_medico VALUES
    (1, 100, 1, 1, '2026-11-01', 'Control de presión', NULL, 'PROGRAMADO'),
    (2, 100, 1, 1, '2026-09-01', 'Control cancelado', NULL, 'CANCELADO'),
    (3, 200, 2, 1, '2026-11-02', 'Control de otro paciente', NULL, 'REALIZADO');
"""


BETA_DATA = """
INSERT INTO historial_clinico VALUES
    (110, 1, '2026-09-03 10:00:00', 'BETA: historial aislado', 1),
    (120, 2, '2026-09-03 10:00:00', 'BETA: segundo paciente', 1);
INSERT INTO antecedente_clinico VALUES
    (11, 110, 'OTRO', 'Solo BETA', '2026-09-03 10:00:00', 1);
"""


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


def _cargar(engine, datos):
    raw = engine.raw_connection()
    try:
        raw.driver_connection.executescript(SCHEMA + USUARIOS + datos)
        raw.commit()
    finally:
        raw.close()


@pytest.fixture
def cliente_cu25(monkeypatch):
    control = _engine()

    @event.listens_for(control, "connect")
    def _adjuntar_control(connection, _record):
        connection.execute("ATTACH DATABASE ':memory:' AS saas_control")

    with control.begin() as connection:
        for model in (Empresa, PlanSaas, Suscripcion, TenantDatabase):
            model.__table__.create(connection)
    with Session(control) as db:
        db.add(PlanSaas(id=1, codigo="PRO", estado=True))
        for tenant_id, codigo in ((1, "ALPHA"), (2, "BETA")):
            db.add_all([
                Empresa(id=tenant_id, codigo=codigo, slug=codigo.lower(), estado="ACTIVA"),
                Suscripcion(
                    id=tenant_id,
                    empresa_id=tenant_id,
                    plan_id=1,
                    estado="ACTIVA",
                    fecha_inicio=date.today() - timedelta(days=30),
                    fecha_fin=date.today() + timedelta(days=365),
                ),
                TenantDatabase(
                    id=tenant_id,
                    empresa_id=tenant_id,
                    database_name=f"tenant_{codigo.lower()}",
                    estado="ACTIVA",
                ),
            ])
        db.commit()

    engines = {"ALPHA": _engine(), "BETA": _engine()}
    _cargar(engines["ALPHA"], ALPHA_DATA)
    _cargar(engines["BETA"], BETA_DATA)

    class Registry:
        calls = []

        def get_session(self, context):
            self.calls.append(context.empresa_codigo)
            return Session(engines[context.empresa_codigo])

    registry = Registry()
    monkeypatch.setattr(clinical_session, "open_control_session", lambda: Session(control))
    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[get_tenant_engine_registry] = lambda: registry
    try:
        with TestClient(app) as client:
            yield client, registry
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)
        for engine in (*engines.values(), control):
            engine.dispose()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _tenant(codigo="ALPHA", usuario_id=10, rol_id=4):
    identificador = 1 if codigo == "ALPHA" else 2
    return crear_tenant_access_token(
        usuario_id,
        rol_id,
        identificador,
        identificador,
        codigo,
        rol_nombre="Paciente" if rol_id == 4 else None,
    )


def _alterar_token(**cambios):
    claims = jwt.decode(
        _tenant(), JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM],
    )
    claims.update(cambios)
    return jwt.encode(claims, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def test_paciente_ve_solo_su_informacion_vigente(cliente_cu25):
    client, _registry = cliente_cu25
    respuesta = client.get(URL, headers=_auth(_tenant()))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    texto = respuesta.text
    assert cuerpo["historial_id"] == 10
    assert cuerpo["observaciones_generales"].startswith("ALPHA")
    assert [item["descripcion"] for item in cuerpo["antecedentes"]] == ["Penicilina"]
    assert [item["id"] for item in cuerpo["consultas"]] == [100]
    consulta = cuerpo["consultas"][0]
    assert [item["nombre"] for item in consulta["diagnosticos"]] == ["Glaucoma"]
    assert [item["descripcion"] for item in consulta["tratamientos"]] == ["Timolol 0.5%"]
    assert consulta["indicaciones"][0]["descripcion"] == "No frotar los ojos"
    assert consulta["recetas"][0]["detalles"][0]["medicamento"] == "Timolol"
    assert consulta["examenes"][0]["resultados"][0]["resultado"] == "PIO 22 mmHg"
    assert [item["motivo"] for item in cuerpo["controles"]] == ["Control de presión"]

    for no_autorizado in (
        "archivo_url", "interno/privado.pdf", "Antecedente retirado",
        "Consulta no autorizada", "Diagnóstico retirado", "Tratamiento retirado",
        "Receta anulada", "Medicamento anulado", "Examen anulado",
        "Resultado retirado", "Control cancelado", "Historial de otro paciente",
        "Antecedente ajeno", "Consulta de otro paciente", "Control de otro paciente",
    ):
        assert no_autorizado not in texto


def test_paciente_no_puede_elegir_otro_paciente(cliente_cu25):
    client, _registry = cliente_cu25
    respuesta = client.get(URL, params={"paciente_id": 2}, headers=_auth(_tenant()))
    assert respuesta.status_code == 200
    assert respuesta.json()["historial_id"] == 10
    assert "Historial de otro paciente" not in respuesta.text


def test_mismo_usuario_y_ids_quedan_aislados_por_tenant(cliente_cu25):
    client, registry = cliente_cu25
    alpha = client.get(URL, headers=_auth(_tenant("ALPHA")))
    beta = client.get(URL, headers=_auth(_tenant("BETA")))
    assert alpha.json()["historial_id"] == 10
    assert beta.json()["historial_id"] == 110
    assert "ALPHA" not in beta.text and "BETA" not in alpha.text
    assert registry.calls == ["ALPHA", "BETA"]


@pytest.mark.parametrize("usuario_id, rol_id", [(7, 1), (9, 3)])
def test_personal_no_paciente_recibe_403(cliente_cu25, usuario_id, rol_id):
    client, _registry = cliente_cu25
    respuesta = client.get(URL, headers=_auth(_tenant(usuario_id=usuario_id, rol_id=rol_id)))
    assert respuesta.status_code == 403


def test_paciente_inactivo_recibe_403(cliente_cu25):
    client, _registry = cliente_cu25
    assert client.get(URL, headers=_auth(_tenant(usuario_id=15))).status_code == 403


def test_usuario_sin_perfil_recibe_404(cliente_cu25):
    client, _registry = cliente_cu25
    assert client.get(URL, headers=_auth(_tenant(usuario_id=16))).status_code == 404


def test_usuario_inactivo_se_rechaza_antes_del_servicio(cliente_cu25):
    client, _registry = cliente_cu25
    assert client.get(URL, headers=_auth(_tenant(usuario_id=17))).status_code == 401


def test_paciente_con_rol_inactivo_recibe_403(cliente_cu25):
    client, _registry = cliente_cu25
    respuesta = client.get(
        URL,
        headers=_auth(_tenant(usuario_id=18, rol_id=5)),
    )
    assert respuesta.status_code == 403


@pytest.mark.parametrize("token", [
    crear_access_token(10, 4),
    crear_saas_access_token(10),
    _alterar_token(exp=datetime.now(timezone.utc) - timedelta(minutes=1)),
    _alterar_token(exp=None),
    _alterar_token() + "manipulado",
])
def test_tokens_no_tenant_invalidos_no_abren_sesion(cliente_cu25, token):
    client, registry = cliente_cu25
    assert client.get(URL, headers=_auth(token)).status_code == 401
    assert registry.calls == []


def test_token_ausente_no_consulta(cliente_cu25):
    client, registry = cliente_cu25
    assert client.get(URL).status_code in (401, 403)
    assert registry.calls == []


def test_ruta_estatica_y_openapi_no_colisionan():
    paths = app.openapi()["paths"]
    assert set(paths[URL]) >= {"get"}
    assert "/historial-clinico/{paciente_id}" in paths
    operation_ids = [
        operation["operationId"]
        for path in paths.values()
        for method, operation in path.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operation_ids) == len(set(operation_ids))
