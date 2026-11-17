"""Autorregistro de pacientes aislado por tenant, sin bases reales."""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.security import verificar_password
from app.core.tenancy.dependencies import get_control_db
from app.core.tenancy.exceptions import TenantConnectionError
from app.core.tenancy.models import Empresa, PlanSaas, Suscripcion, TenantDatabase
from app.core.tenancy.registry_provider import get_tenant_engine_registry
from app.main import app
from app.modules.gestion_historial_clinico.models.models import HistorialClinico
from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora, Usuario
from app.modules.gestion_usuarios_seguridad.schemas.schemas import RegistroPacienteRequest
from app.modules.gestion_usuarios_seguridad.services import registro_paciente_service


def _tenant_engine(role_id: int):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    ddls = [
        """CREATE TABLE rol (
            id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT,
            estado BOOLEAN NOT NULL, protegido BOOLEAN NOT NULL,
            fecha_creacion TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE usuario (
            id INTEGER PRIMARY KEY, correo TEXT NOT NULL,
            password_hash TEXT NOT NULL, estado BOOLEAN NOT NULL,
            fecha_creacion TIMESTAMP NOT NULL,
            rol_id INTEGER NOT NULL REFERENCES rol(id)
        )""",
        """CREATE TABLE paciente (
            id INTEGER PRIMARY KEY,
            usuario_id INTEGER UNIQUE REFERENCES usuario(id),
            nombres TEXT NOT NULL, apellidos TEXT NOT NULL, ci TEXT,
            fecha_nacimiento DATE, sexo TEXT, telefono TEXT,
            contacto_emergencia TEXT, fecha_registro TIMESTAMP NOT NULL,
            direccion TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE historial_clinico (
            id INTEGER PRIMARY KEY,
            paciente_id INTEGER NOT NULL UNIQUE REFERENCES paciente(id),
            fecha_apertura TIMESTAMP NOT NULL,
            observaciones_generales TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE bitacora (
            id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
            fecha_hora TIMESTAMP NOT NULL, ip TEXT, accion TEXT NOT NULL,
            entidad_afectada TEXT, id_registro_afectado INTEGER,
            descripcion TEXT
        )""",
    ]
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in ddls:
            connection.exec_driver_sql(ddl)
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_usuario_correo_test "
            "ON usuario(lower(trim(correo)))"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_paciente_ci_test "
            "ON paciente(ci) WHERE ci IS NOT NULL"
        )
        connection.execute(text("""
            INSERT INTO rol
                (id, nombre, descripcion, estado, protegido, fecha_creacion)
            VALUES (:id, 'Paciente', NULL, 1, 0, '2026-01-01')
        """), {"id": role_id})
        connection.execute(text("""
            INSERT INTO usuario
                (id, correo, password_hash, estado, fecha_creacion, rol_id)
            VALUES (10, 'vinculado@example.test', 'hash-existente', 1,
                    '2026-01-01', :rol_id)
        """), {"rol_id": role_id})
        connection.exec_driver_sql("""
            INSERT INTO paciente
                (id, usuario_id, nombres, apellidos, ci, fecha_nacimiento,
                 sexo, telefono, fecha_registro, estado)
            VALUES
                (10, 10, 'Paciente', 'Vinculado', 'CI-VINCULADO',
                 '1990-01-01', 'F', '70000001', '2026-01-01', 1),
                (11, NULL, 'Paciente', 'Sin Cuenta', 'CI-SIN-CUENTA',
                 '1991-01-01', 'M', '70000002', '2026-01-01', 1)
        """)
    return engine


def _control_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def attach_control(dbapi_connection, _):
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS saas_control")

    with engine.begin() as connection:
        for model in (Empresa, PlanSaas, Suscripcion, TenantDatabase):
            model.__table__.create(connection)

    with Session(engine) as db:
        db.add(PlanSaas(id=1, codigo="PRO", estado=True))
        for company_id, code in ((1, "ALPHA"), (2, "BETA")):
            db.add_all([
                Empresa(
                    id=company_id,
                    codigo=code,
                    slug=code.lower(),
                    nombre_comercial=f"Clínica {code}",
                    razon_social=f"Clínica {code} SRL",
                    estado="ACTIVA",
                ),
                Suscripcion(
                    id=company_id,
                    empresa_id=company_id,
                    plan_id=1,
                    fecha_inicio=date(2026, 1, 1),
                    fecha_fin=date(2026, 12, 31),
                    estado="ACTIVA",
                ),
                TenantDatabase(
                    id=company_id,
                    empresa_id=company_id,
                    database_name=f"tenant_{code.lower()}",
                    estado="ACTIVA",
                    version_schema="v2-cu31",
                ),
            ])
        db.commit()
    return engine


class _TrackingSession(Session):
    closed_by_service = False

    def close(self):
        self.closed_by_service = True
        super().close()


class _Registry:
    def __init__(self, engines):
        self.engines = engines
        self.sessions = []

    def get_session(self, context):
        session = _TrackingSession(bind=self.engines[context.database_name])
        self.sessions.append(session)
        return session


@pytest.fixture
def tenant_registration_env():
    control = _control_engine()
    alpha = _tenant_engine(91)
    beta = _tenant_engine(73)
    legacy = _tenant_engine(4)
    registry = _Registry({"tenant_alpha": alpha, "tenant_beta": beta})

    def control_db():
        with Session(control) as db:
            yield db

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_control_db] = control_db
    app.dependency_overrides[get_tenant_engine_registry] = lambda: registry
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield SimpleNamespace(
                client=client,
                control=control,
                alpha=alpha,
                beta=beta,
                legacy=legacy,
                registry=registry,
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        for engine in (control, alpha, beta, legacy):
            engine.dispose()


def _payload(company="ALPHA", **changes):
    values = {
        "empresa_codigo": company,
        "ci": "CI-NUEVO-001",
        "nombres": "Paciente",
        "apellidos": "Tenant",
        "fecha_nacimiento": "1998-07-07",
        "sexo": "F",
        "telefono": "70080807",
        "contacto_emergencia": "Familiar 70000000",
        "direccion": "Dirección de prueba",
        "correo": "nuevo.tenant@example.test",
        "password": "ClaveSegura123!",
    }
    values.update(changes)
    return values


def _count(engine, model):
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(model))


def test_registro_tenant_crea_usuario_paciente_historial_y_bitacora(
    tenant_registration_env,
):
    env = tenant_registration_env
    response = env.client.post(
        "/seguridad/tenant/registro-paciente",
        json=_payload(company=" alpha "),
    )
    assert response.status_code == 201
    assert response.json() == {"message": "Cuenta creada correctamente"}

    with Session(env.alpha) as db:
        usuario = db.scalar(select(Usuario).where(
            Usuario.correo == "nuevo.tenant@example.test",
        ))
        paciente = db.scalar(select(Paciente).where(
            Paciente.usuario_id == usuario.id,
        ))
        historial = db.scalar(select(HistorialClinico).where(
            HistorialClinico.paciente_id == paciente.id,
        ))
        bitacora = db.scalar(select(Bitacora).where(
            Bitacora.id_registro_afectado == paciente.id,
        ))

        assert usuario.rol_id == 91
        assert verificar_password("ClaveSegura123!", usuario.password_hash)
        assert paciente.ci == "CI-NUEVO-001"
        assert historial is not None and historial.estado is True
        assert bitacora.accion == "REGISTRO_PACIENTE"
    assert env.registry.sessions[-1].closed_by_service is True


def test_registro_en_dos_clinicas_es_independiente_y_no_toca_legacy(
    tenant_registration_env,
):
    env = tenant_registration_env
    legacy_before = _count(env.legacy, Usuario)

    assert env.client.post(
        "/seguridad/tenant/registro-paciente", json=_payload("ALPHA"),
    ).status_code == 201
    assert env.client.post(
        "/seguridad/tenant/registro-paciente", json=_payload("BETA"),
    ).status_code == 201

    assert _count(env.alpha, Usuario) == 2
    assert _count(env.beta, Usuario) == 2
    assert _count(env.legacy, Usuario) == legacy_before
    with Session(env.beta) as db:
        nuevo = db.scalar(select(Usuario).where(
            Usuario.correo == "nuevo.tenant@example.test",
        ))
        assert nuevo.rol_id == 73


@pytest.mark.parametrize("state", ["SUSPENDIDA", "PENDIENTE"])
def test_empresa_inexistente_o_inactiva_no_abre_tenant(
    tenant_registration_env, state,
):
    env = tenant_registration_env
    with Session(env.control) as db:
        db.get(Empresa, 1).estado = state
        db.commit()
    response = env.client.post(
        "/seguridad/tenant/registro-paciente", json=_payload("ALPHA"),
    )
    assert response.status_code == 404
    assert not env.registry.sessions

    missing = env.client.post(
        "/seguridad/tenant/registro-paciente", json=_payload("NO-EXISTE"),
    )
    assert missing.status_code == 404


@pytest.mark.parametrize("change", ["suspendida", "vencida"])
def test_suscripcion_no_vigente_rechaza_registro(
    tenant_registration_env, change,
):
    env = tenant_registration_env
    with Session(env.control) as db:
        subscription = db.get(Suscripcion, 1)
        if change == "suspendida":
            subscription.estado = "SUSPENDIDA"
        else:
            subscription.fecha_fin = date(2026, 1, 31)
        db.commit()

    response = env.client.post(
        "/seguridad/tenant/registro-paciente", json=_payload("ALPHA"),
    )
    assert response.status_code == 403
    assert not env.registry.sessions


def test_base_tenant_no_disponible_rechaza_sin_abrir_sesion(
    tenant_registration_env,
):
    env = tenant_registration_env
    with Session(env.control) as db:
        db.get(TenantDatabase, 1).estado = "PENDIENTE"
        db.commit()

    response = env.client.post(
        "/seguridad/tenant/registro-paciente", json=_payload("ALPHA"),
    )
    assert response.status_code == 503
    assert not env.registry.sessions


def test_fallo_de_conexion_tenant_devuelve_error_seguro(
    tenant_registration_env, monkeypatch,
):
    env = tenant_registration_env

    def unavailable(_context):
        raise TenantConnectionError("private connection detail")

    monkeypatch.setattr(env.registry, "get_session", unavailable)
    response = env.client.post(
        "/seguridad/tenant/registro-paciente", json=_payload("ALPHA"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "La clínica no está disponible temporalmente",
    }
    assert "private" not in response.text


def test_correo_duplicado_se_rechaza_en_la_misma_clinica(
    tenant_registration_env,
):
    response = tenant_registration_env.client.post(
        "/seguridad/tenant/registro-paciente",
        json=_payload(correo=" VINCULADO@example.test ", ci="CI-OTRO"),
    )
    assert response.status_code == 409
    assert "correo" in response.json()["detail"].lower()


def test_conflicto_de_unicidad_despues_del_precheck_hace_rollback(
    tenant_registration_env, monkeypatch,
):
    env = tenant_registration_env
    before = tuple(_count(env.alpha, model) for model in (Usuario, Paciente))
    monkeypatch.setattr(
        registro_paciente_service.repo,
        "obtener_usuario_por_correo",
        lambda *_args, **_kwargs: None,
    )

    response = env.client.post(
        "/seguridad/tenant/registro-paciente",
        json=_payload(correo="vinculado@example.test", ci="CI-RACE"),
    )

    assert response.status_code == 409
    assert "datos ya existen" in response.json()["detail"]
    assert tuple(_count(env.alpha, model) for model in (Usuario, Paciente)) == before


def test_rol_paciente_inactivo_impide_registro(tenant_registration_env):
    env = tenant_registration_env
    with env.alpha.begin() as connection:
        connection.execute(text("UPDATE rol SET estado = 0 WHERE id = 91"))

    response = env.client.post(
        "/seguridad/tenant/registro-paciente", json=_payload(),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "No fue posible crear la cuenta."}
    assert _count(env.alpha, Usuario) == 1


@pytest.mark.parametrize(
    ("ci", "ci_guardado"),
    [
        ("CI-VINCULADO", "CI-VINCULADO"),
        ("ci-sin-cuenta", "CI-SIN-CUENTA"),
    ],
)
def test_paciente_existente_exige_verificacion_sin_apropiar_expediente(
    tenant_registration_env, ci, ci_guardado,
):
    env = tenant_registration_env
    before_users = _count(env.alpha, Usuario)
    response = env.client.post(
        "/seguridad/tenant/registro-paciente",
        json=_payload(ci=ci, correo=f"{ci.lower()}@example.test"),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == registro_paciente_service.MENSAJE_PACIENTE_EXISTENTE
    assert _count(env.alpha, Usuario) == before_users
    with Session(env.alpha) as db:
        paciente = db.scalar(select(Paciente).where(Paciente.ci == ci_guardado))
        if ci_guardado == "CI-SIN-CUENTA":
            assert paciente.usuario_id is None


def test_validaciones_y_campos_controlados_se_rechazan(
    tenant_registration_env,
):
    client = tenant_registration_env.client
    assert client.post(
        "/seguridad/tenant/registro-paciente",
        json=_payload(password="debil"),
    ).status_code == 422
    incomplete = _payload()
    incomplete.pop("ci")
    assert client.post(
        "/seguridad/tenant/registro-paciente", json=incomplete,
    ).status_code == 422
    for field, value in (
        ("rol_id", 91), ("usuario_id", 8),
        ("tenant_id", 1), ("database_name", "tenant_alpha"),
    ):
        assert client.post(
            "/seguridad/tenant/registro-paciente",
            json=_payload(**{field: value}),
        ).status_code == 422


def test_rollback_total_si_falla_bitacora(
    tenant_registration_env, monkeypatch,
):
    env = tenant_registration_env
    before = tuple(
        _count(env.alpha, model)
        for model in (Usuario, Paciente, HistorialClinico, Bitacora)
    )

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        registro_paciente_service.repo, "registrar_bitacora", fail_audit,
    )
    response = env.client.post(
        "/seguridad/tenant/registro-paciente", json=_payload(),
    )

    assert response.status_code == 500
    assert tuple(
        _count(env.alpha, model)
        for model in (Usuario, Paciente, HistorialClinico, Bitacora)
    ) == before
    assert env.registry.sessions[-1].closed_by_service is True


def test_bloqueos_postgresql_serializan_correo_y_ci():
    calls = []

    class FakeDb:
        @staticmethod
        def get_bind():
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        @staticmethod
        def execute(statement, parameters):
            calls.append((str(statement), parameters["clave"]))

    datos = RegistroPacienteRequest(**{
        key: value for key, value in _payload().items()
        if key != "empresa_codigo"
    })
    registro_paciente_service._bloquear_claves_autorregistro(FakeDb(), datos)

    assert len(calls) == 2
    assert all("pg_advisory_xact_lock" in sql for sql, _ in calls)
    assert [key for _, key in calls] == sorted(key for _, key in calls)


def test_respuestas_no_exponen_base_ni_credenciales(tenant_registration_env):
    response = tenant_registration_env.client.post(
        "/seguridad/tenant/registro-paciente",
        json=_payload("NO-EXISTE"),
    )
    body = response.text.lower()
    assert response.status_code == 404
    assert all(term not in body for term in (
        "database", "tenant_alpha", "password", "hash", "postgresql",
    ))