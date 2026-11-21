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


URL = "/servicios-oftalmologicos/catalogo/"


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
def cliente_catalogo(monkeypatch):
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
    from app.modules.gestion_historial_clinico.models.models import ServicioOftalmologico
    from decimal import Decimal
    for codigo, engine in engines.items():
        ServicioOftalmologico.__table__.create(engine)
        with Session(engine) as db:
            db.add_all([
                ServicioOftalmologico(id=1,nombre=f"{codigo} Consulta",descripcion=f"Solo {codigo}",precio=Decimal("100.50"),duracion=30,estado=True),
                ServicioOftalmologico(id=2,nombre=f"{codigo} Retirado",estado=False),
                ServicioOftalmologico(id=3,nombre="Servicio 100% especial",estado=True),
            ])
            if codigo == "BETA":
                db.add(ServicioOftalmologico(id=9,nombre="Exclusivo BETA",estado=True))
            db.commit()


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



def test_catalogos_aislados_con_ids_iguales(cliente_catalogo):
    client, registry = cliente_catalogo
    for empresa in ("ALPHA", "BETA"):
        respuesta = client.get(URL, headers=_auth(_tenant(empresa)))
        assert respuesta.status_code == 200
        assert len(respuesta.json()) == (2 if empresa == "ALPHA" else 3)
        assert ("BETA" if empresa == "ALPHA" else "ALPHA") not in respuesta.text
        assert "Retirado" not in respuesta.text
        detalle = client.get(URL+"1", headers=_auth(_tenant(empresa)))
        assert detalle.json()["nombre"] == f"{empresa} Consulta"
        assert detalle.json()["precio_base"] == 100.5
    assert registry.calls == ["ALPHA", "ALPHA", "BETA", "BETA"]

def test_no_acepta_empresa_cliente(cliente_catalogo):
    client, _ = cliente_catalogo
    respuesta = client.get(URL,params={"empresa_codigo":"BETA","empresa_id":2},headers=_auth(_tenant()))
    assert respuesta.status_code == 200
    assert "BETA" not in respuesta.text
    assert client.get(URL+"9",headers=_auth(_tenant())).status_code == 404

@pytest.mark.parametrize("id", [2,999])
def test_inactivo_o_inexistente_404(cliente_catalogo,id):
    client,_=cliente_catalogo
    assert client.get(URL+str(id),headers=_auth(_tenant())).status_code == 404

def test_filtro_paginacion_y_comodin_literal(cliente_catalogo):
    client,_=cliente_catalogo
    auth=_auth(_tenant())
    respuesta=client.get(URL,params={"busqueda":"  alpha  "},headers=auth)
    assert [s["id"] for s in respuesta.json()] == [1]
    assert client.get(URL,params={"busqueda":"%"},headers=auth).json()[0]["id"] == 3
    assert client.get(URL,params={"busqueda":"ausente"},headers=auth).json() == []
    assert len(client.get(URL,params={"limite":1,"offset":1},headers=auth).json()) == 1
    assert client.get(URL,params={"offset":99},headers=auth).json() == []

@pytest.mark.parametrize("query",[{"limite":0},{"limite":101},{"offset":-1},{"busqueda":"x"*151}])
def test_valida_parametros(cliente_catalogo,query):
    client,_=cliente_catalogo
    assert client.get(URL,params=query,headers=_auth(_tenant())).status_code == 422

@pytest.mark.parametrize("usuario,rol",[(7,1),(9,3),(18,5)])
def test_rol_no_paciente_o_inactivo(cliente_catalogo,usuario,rol):
    client,_=cliente_catalogo
    assert client.get(URL,headers=_auth(_tenant(usuario_id=usuario,rol_id=rol))).status_code == 403

@pytest.mark.parametrize("token",[
    crear_access_token(10,4),crear_saas_access_token(10),
    _alterar_token(exp=datetime.now(timezone.utc)-timedelta(minutes=1)),
    _alterar_token(empresa_id=2),
    _alterar_token()+"alterado",
])
def test_rechaza_token_invalido_sin_mezclar_empresas(cliente_catalogo,token):
    client,registry=cliente_catalogo
    assert client.get(URL,headers=_auth(token)).status_code == 401
    assert registry.calls == []

def test_sin_token(cliente_catalogo):
    client,registry=cliente_catalogo
    assert client.get(URL).status_code in (401,403)
    assert registry.calls == []
