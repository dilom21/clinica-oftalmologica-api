"""CU36 audit I-1..I-7: regression coverage for the final integration fixes.

Cada bloque referencia el hallazgo de auditoria que protege:

* I-2: refunds Stripe sin metadata se enrutan por la metadata confiable del
  PaymentIntent o quedan en cuarentena central; nunca en la base legacy.
* I-3: ``payment_intent.canceled`` pasa por el libro CU36 y no anula APROBADOS.
* I-4: el provisionamiento deriva la version del manifiesto y verifica el
  esquema fisico antes de etiquetar un tenant.
* I-5: el preflight RBAC acepta todas las versiones soportadas (v5/v6).
* I-6: el runbook usa la variable real del migrador.
* I-7: el SQL externo CU26 coincide byte a byte con el manifiesto (LF).
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.administracion_saas.models import StripeWebhookHuerfanoCentral
from app.modules.administracion_saas.tenant_migrator import load_bundle
from app.modules.gestion_pagos.models.models import (
    Pago, PagoAjuste, StripeWebhookHuerfano,
)
from app.modules.gestion_pagos.providers import (
    ErrorProveedorPagoDesconocido, IntentoPagoProveedor,
)
from app.modules.gestion_pagos.repositories import webhooks_huerfanos as huerfanos_repo
from app.modules.gestion_pagos.services import huerfanos as huerfanos_service
from app.modules.gestion_pagos.services import service as pagos_service
from app.modules.gestion_pagos.services import webhook_tenancy
from app.modules.gestion_pagos.services.webhook_tenancy import (
    StripeWebhookScope, StripeWebhookSessionResolver,
)
from scripts.saas import preflight_rbac_cu26_cu27
from scripts.saas import provision_tenant
from tests.test_base_flujo_pagos import db_pagos  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


class ProveedorIntencion:
    """Provider double that only exposes PaymentIntent metadata."""

    def __init__(self, metadata=None, *, error=None):
        self.metadata = dict(metadata or {})
        self.error = error
        self.consultas: list[str] = []

    def recuperar_intencion(self, payment_intent_id):
        self.consultas.append(payment_intent_id)
        if self.error is not None:
            raise self.error
        return IntentoPagoProveedor(
            id=payment_intent_id, client_secret="s", amount=2020,
            currency="bob", status="succeeded", metadata=self.metadata,
        )


def _evento_refund(*, refund_id="re_1", pi="pi_1", metadata=None, tipo="refund.created"):
    return {
        "id": f"evt_{refund_id}",
        "type": tipo,
        "created": 1_760_000_000,
        "data": {"object": {
            "id": refund_id, "payment_intent": pi, "amount": 2020,
            "currency": "bob", "status": "succeeded",
            "metadata": dict(metadata or {}),
        }},
    }


def _resolver() -> StripeWebhookSessionResolver:
    return StripeWebhookSessionResolver(
        registry=object(),
        control_session_factory=lambda: None,
        legacy_session_factory=lambda: None,
    )


def _metadata_tenant(**extra):
    values = {
        "clinica_contexto": "tenant", "empresa_id": "1", "empresa_codigo": "CLINICA_A",
        "tenant_id": "10", "pago_id": "900", "consulta_clinica_id": "100",
    }
    values.update(extra)
    return values


# --------------------------------------------------------------------- I-2
def test_i2_refund_sin_metadata_usa_la_metadata_del_payment_intent(monkeypatch):
    class FakeResolver:
        def __init__(self, repository):
            pass

        def resolver_por_codigo_empresa(self, codigo):
            return SimpleNamespace(
                empresa_id=1, tenant_database_id=10, empresa_codigo=codigo,
            )

        def require_active_database(self, context):
            return None

    monkeypatch.setattr(webhook_tenancy, "TenantResolver", FakeResolver)
    proveedor = ProveedorIntencion(_metadata_tenant())
    scope = _resolver()._resolve_scope(_evento_refund(), proveedor)

    assert proveedor.consultas == ["pi_1"]
    assert scope.is_tenant is True
    assert scope.is_orphan is False
    assert scope.tenant_context.empresa_codigo == "CLINICA_A"
    assert scope.metadata["pago_id"] == "900"


def test_i2_payment_intent_sin_metadata_va_a_cuarentena_central():
    proveedor = ProveedorIntencion({})
    scope = _resolver()._resolve_scope(_evento_refund(), proveedor)
    assert scope.is_tenant is False
    assert scope.is_orphan is True


def test_i2_sin_provider_o_sin_payment_intent_no_es_legacy():
    assert _resolver()._resolve_scope(_evento_refund()).is_orphan is True
    sin_pi = _evento_refund(pi=None)
    assert _resolver()._resolve_scope(sin_pi, ProveedorIntencion()).is_orphan is True


def test_i2_metadata_parcial_del_payment_intent_es_ambigua(monkeypatch):
    proveedor = ProveedorIntencion({"clinica_contexto": "tenant", "empresa_id": "1"})
    with pytest.raises(HTTPException) as error:
        _resolver()._resolve_scope(_evento_refund(), proveedor)
    assert error.value.status_code == 409


def test_i2_resultado_desconocido_del_proveedor_no_se_atribuye():
    proveedor = ProveedorIntencion(error=ErrorProveedorPagoDesconocido("timeout"))
    with pytest.raises(HTTPException) as error:
        _resolver()._resolve_scope(_evento_refund(), proveedor)
    assert error.value.status_code == 503


def test_i2_evento_no_refund_sin_metadata_sigue_siendo_legacy():
    evento = {
        "id": "evt_old", "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_old", "metadata": {}}},
    }
    scope = _resolver()._resolve_scope(evento)
    assert scope.is_tenant is False
    assert scope.is_orphan is False


def test_i2_orphan_abre_la_sesion_de_control_y_nunca_la_legacy():
    class FakeSession:
        def __init__(self):
            self.info = {}
            self.closed = False

        def close(self):
            self.closed = True

    control = FakeSession()
    legacy = FakeSession()
    resolver = StripeWebhookSessionResolver(
        registry=object(),
        control_session_factory=lambda: control,
        legacy_session_factory=lambda: legacy,
    )
    with resolver.open_for_verified_event(_evento_refund()) as (db, scope):
        assert db is control
        assert scope.is_orphan is True
        assert scope.is_tenant is False
    assert control.closed is True
    assert legacy.closed is False


def _central_db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS saas_control")
        conn.exec_driver_sql("""
            CREATE TABLE saas_control.stripe_webhook_huerfano (
                id INTEGER PRIMARY KEY,
                stripe_event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL, refund_id TEXT NOT NULL,
                payment_intent_id TEXT, amount INTEGER NOT NULL,
                currency TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'PENDIENTE',
                empresa_id INTEGER, tenant_database_id INTEGER, ajuste_id INTEGER,
                motivo TEXT, atribuido_por INTEGER,
                creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actualizado_en TIMESTAMP
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE saas_control.saas_bitacora (
                id INTEGER PRIMARY KEY,
                saas_usuario_id INTEGER,
                fecha_hora TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ip TEXT, accion TEXT NOT NULL, entidad_afectada TEXT,
                id_registro_afectado INTEGER, descripcion TEXT, resultado TEXT
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE stripe_webhook_huerfano (
                id INTEGER PRIMARY KEY,
                stripe_event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL, refund_id TEXT NOT NULL,
                payment_intent_id TEXT, amount INTEGER NOT NULL,
                currency TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'PENDIENTE',
                ajuste_id INTEGER, motivo TEXT,
                creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actualizado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
    return Session(engine, autoflush=False)


def test_i2_orphan_refund_se_persiste_en_cuarentena_central():
    db = _central_db()
    try:
        respuesta = pagos_service.procesar_evento_webhook_stripe(
            db, _evento_refund(), StripeWebhookScope(
                is_tenant=False, metadata={}, is_orphan=True,
            ),
        )
        assert respuesta.procesado is False
        filas = db.scalars(select(StripeWebhookHuerfanoCentral)).all()
        assert len(filas) == 1
        assert filas[0].estado == "PENDIENTE"
        assert filas[0].empresa_id is None
        assert filas[0].refund_id == "re_1"
        assert filas[0].amount == 2020
        # La tabla del tenant (y la base legacy) no recibe nada.
        assert db.scalar(
            select(func.count()).select_from(StripeWebhookHuerfano)
        ) == 0

        repetido = pagos_service.procesar_evento_webhook_stripe(
            db, _evento_refund(), StripeWebhookScope(
                is_tenant=False, metadata={}, is_orphan=True,
            ),
        )
        assert repetido.procesado is False
        assert db.scalar(
            select(func.count()).select_from(StripeWebhookHuerfanoCentral)
        ) == 1
    finally:
        db.close()


def test_i2_sin_persistencia_durable_no_hay_2xx(monkeypatch):
    db = _central_db()
    try:
        def falla(*args, **kwargs):
            raise RuntimeError("fallo de persistencia central")

        monkeypatch.setattr(huerfanos_repo, "registrar_huerfano_central", falla)
        with pytest.raises(RuntimeError):
            pagos_service.procesar_evento_webhook_stripe(
                db, _evento_refund(), StripeWebhookScope(
                    is_tenant=False, metadata={}, is_orphan=True,
                ),
            )
    finally:
        db.close()


def test_i2_listar_central_solo_pendientes():
    db = _central_db()
    try:
        huerfanos_service.registrar_evento_huerfano_central(db, _evento_refund())
        pendientes = huerfanos_service.listar_huerfanos_central(db)
        assert len(pendientes) == 1
        assert pendientes[0].estado == "PENDIENTE"
        assert pendientes[0].empresa_id is None
    finally:
        db.close()


def _central_and_tenant_db():
    central_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with central_engine.begin() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS saas_control")
        conn.exec_driver_sql("""
            CREATE TABLE saas_control.stripe_webhook_huerfano (
                id INTEGER PRIMARY KEY,
                stripe_event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL, refund_id TEXT NOT NULL,
                payment_intent_id TEXT, amount INTEGER NOT NULL,
                currency TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'PENDIENTE',
                empresa_id INTEGER, tenant_database_id INTEGER, ajuste_id INTEGER,
                motivo TEXT, atribuido_por INTEGER,
                creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actualizado_en TIMESTAMP
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE saas_control.saas_bitacora (
                id INTEGER PRIMARY KEY,
                saas_usuario_id INTEGER,
                fecha_hora TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ip TEXT, accion TEXT NOT NULL, entidad_afectada TEXT,
                id_registro_afectado INTEGER, descripcion TEXT, resultado TEXT
            )
        """)
    tenant_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with tenant_engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE stripe_webhook_huerfano (
                id INTEGER PRIMARY KEY,
                stripe_event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL, refund_id TEXT NOT NULL,
                payment_intent_id TEXT, amount INTEGER NOT NULL,
                currency TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'PENDIENTE',
                ajuste_id INTEGER, motivo TEXT,
                creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actualizado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE pago (
                id INTEGER PRIMARY KEY,
                fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                fecha_hora_pago TIMESTAMP,
                monto NUMERIC(12,2) NOT NULL, moneda TEXT NOT NULL,
                metodo_pago TEXT NOT NULL, estado TEXT NOT NULL,
                pasarela TEXT, referencia_transaccion TEXT UNIQUE,
                observaciones TEXT, clave_idempotencia TEXT UNIQUE
            )
        """)
        conn.exec_driver_sql(
            "INSERT INTO pago (id, monto, moneda, metodo_pago, estado, pasarela, "
            "referencia_transaccion) VALUES "
            "(900, 20.20, 'BOB', 'TARJETA', 'APROBADO', 'STRIPE', 'pi_attr')"
        )
    return Session(central_engine, autoflush=False), Session(tenant_engine, autoflush=False)


class _Registry:
    def __init__(self, session):
        self.session = session

    def get_session(self, context):
        return self.session


def _fake_resolver(monkeypatch):
    from app.core.tenancy import resolver as resolver_module

    class FakeResolver:
        def __init__(self, repository):
            pass

        def resolver_por_codigo_empresa(self, codigo):
            return SimpleNamespace(
                empresa_id=7, tenant_database_id=70, empresa_codigo=codigo,
            )

        def require_active_database(self, context):
            return None

    monkeypatch.setattr(resolver_module, "TenantResolver", FakeResolver)


def test_i2_atribucion_central_copia_al_tenant_y_es_idempotente(monkeypatch):
    from app.modules.gestion_pagos.repositories import saldos

    _fake_resolver(monkeypatch)
    monkeypatch.setattr(
        saldos, "monto_vigente_pago", lambda db, pago: Decimal("20.20"),
    )
    db, tenant_db = _central_and_tenant_db()
    try:
        central = huerfanos_service.registrar_evento_huerfano_central(
            db, _evento_refund(pi="pi_attr"),
        )
        datos = huerfanos_service.AtribuirHuerfanoCentral(
            empresa_codigo="CLINICA_A",
            motivo="Refund del dashboard identificado por arqueo",
            evidencia="Arqueo 2026-11-17 / captura Stripe",
        )
        respuesta = huerfanos_service.atribuir_huerfano_central(
            db, central.id, datos,
            SimpleNamespace(id=99, rol="SUPERADMIN"),
            _Registry(tenant_db),
        )
        assert respuesta.estado == "ATRIBUIDO"
        assert respuesta.empresa_id == 7
        assert respuesta.tenant_database_id == 70
        assert respuesta.atribuido_por == 99

        tenant_db2 = Session(tenant_db.get_bind(), autoflush=False)
        filas = tenant_db2.scalars(select(StripeWebhookHuerfano)).all()
        assert len(filas) == 1
        assert filas[0].stripe_event_id == "evt_re_1"
        assert "Atribuido desde cuarentena central" in filas[0].motivo
        tenant_db2.close()

        repetido = huerfanos_service.atribuir_huerfano_central(
            db, central.id, datos,
            SimpleNamespace(id=99, rol="SUPERADMIN"),
            _Registry(Session(tenant_db.get_bind(), autoflush=False)),
        )
        assert repetido.estado == "ATRIBUIDO"
    finally:
        tenant_db.close()
        db.close()


def test_i2_atribucion_ambigua_se_rechaza(monkeypatch):
    from app.modules.gestion_pagos.repositories import saldos

    _fake_resolver(monkeypatch)
    monkeypatch.setattr(
        saldos, "monto_vigente_pago", lambda db, pago: Decimal("20.20"),
    )
    db, tenant_db = _central_and_tenant_db()
    try:
        central = huerfanos_service.registrar_evento_huerfano_central(
            db, _evento_refund(pi="pi_otro"),
        )
        datos = huerfanos_service.AtribuirHuerfanoCentral(
            empresa_codigo="CLINICA_A",
            motivo="Intento de atribucion sin respaldo",
            evidencia="Sin evidencia suficiente",
        )
        with pytest.raises(HTTPException) as error:
            huerfanos_service.atribuir_huerfano_central(
                db, central.id, datos,
                SimpleNamespace(id=99, rol="SUPERADMIN"),
                _Registry(tenant_db),
            )
        assert error.value.status_code == 409
        assert "ambigua" in error.value.detail
        assert huerfanos_repo.obtener_huerfano_central(db, central.id).estado == "PENDIENTE"
    finally:
        tenant_db.close()
        db.close()


# --------------------------------------------------------------------- I-3
def _evento_intent(tipo, *, pi, amount=2020, currency="bob", event_id="evt_cancel"):
    return {
        "id": event_id,
        "type": tipo,
        "created": 1_760_000_000,
        "data": {"object": {
            "id": pi, "amount": amount, "currency": currency, "metadata": {},
        }},
    }


def test_i3_canceled_de_pendiente_crea_ajuste_y_bitacora(db_pagos):  # noqa: F811
    evento = _evento_intent(
        "payment_intent.canceled", pi="p", amount=1010, event_id="evt_c1",
    )
    respuesta = pagos_service.procesar_evento_webhook_stripe(
        db_pagos, evento, StripeWebhookScope(is_tenant=False, metadata={}),
    )
    assert respuesta.procesado is True
    pago = db_pagos.get(Pago, 501)
    assert pago.estado == "ANULADO"
    ajuste = db_pagos.scalar(
        select(PagoAjuste).where(PagoAjuste.pago_id == 501)
    )
    assert ajuste is not None
    assert ajuste.tipo == "ANULACION"
    assert ajuste.estado == "CONFIRMADO"
    assert ajuste.importe == Decimal("0.00")
    bitacora = db_pagos.execute(text(
        "SELECT accion FROM bitacora WHERE id_registro_afectado = 501 "
        "AND accion = 'ANULAR_PAGO_PROVEEDOR_CANCELADO'"
    )).all()
    assert len(bitacora) == 1


def test_i3_canceled_sobre_aprobado_no_anula_y_abre_discrepancia(db_pagos):  # noqa: F811
    evento = _evento_intent(
        "payment_intent.canceled", pi="ok", amount=2020, event_id="evt_c2",
    )
    primera = pagos_service.procesar_evento_webhook_stripe(
        db_pagos, evento, StripeWebhookScope(is_tenant=False, metadata={}),
    )
    assert primera.procesado is True
    assert db_pagos.get(Pago, 500).estado == "APROBADO"
    ajustes = db_pagos.scalars(
        select(PagoAjuste).where(PagoAjuste.pago_id == 500)
    ).all()
    assert len(ajustes) == 1
    assert ajustes[0].tipo == "RECONCILIACION"
    assert ajustes[0].estado == "PENDIENTE_PROVEEDOR"
    assert ajustes[0].importe == Decimal("0.00")

    # Reentrega: 2xx idempotente, sin duplicar y sin reintentos infinitos.
    segunda = pagos_service.procesar_evento_webhook_stripe(
        db_pagos, evento, StripeWebhookScope(is_tenant=False, metadata={}),
    )
    assert segunda.procesado is True
    assert db_pagos.scalar(
        select(func.count()).select_from(PagoAjuste).where(
            PagoAjuste.pago_id == 500)
    ) == 1


def test_i3_canceled_de_rechazado_usa_el_libro(db_pagos):  # noqa: F811
    evento = _evento_intent(
        "payment_intent.canceled", pi="r", amount=1010, event_id="evt_c3",
    )
    pagos_service.procesar_evento_webhook_stripe(
        db_pagos, evento, StripeWebhookScope(is_tenant=False, metadata={}),
    )
    assert db_pagos.get(Pago, 502).estado == "ANULADO"
    assert db_pagos.scalar(
        select(PagoAjuste).where(PagoAjuste.pago_id == 502)
    ).tipo == "ANULACION"


def test_i3_canceled_repetido_sobre_anulado_no_duplica(db_pagos):  # noqa: F811
    evento = _evento_intent(
        "payment_intent.canceled", pi="a", amount=1010, event_id="evt_c4",
    )
    respuesta = pagos_service.procesar_evento_webhook_stripe(
        db_pagos, evento, StripeWebhookScope(is_tenant=False, metadata={}),
    )
    assert respuesta.procesado is True
    assert db_pagos.get(Pago, 503).estado == "ANULADO"
    assert db_pagos.scalar(
        select(func.count()).select_from(PagoAjuste).where(
            PagoAjuste.pago_id == 503)
    ) == 0


# --------------------------------------------------------------------- I-4
def test_i4_schema_version_viene_del_manifiesto():
    bundle = load_bundle(ROOT / "database" / "tenant_migrations")
    assert provision_tenant.SCHEMA_VERSION == bundle.schema_version
    assert provision_tenant.SCHEMA_VERSION != "v4-sprint3-cu32-cu33"


def _backend(runner):
    from scripts.saas.provision_tenant import (
        PostgresConnection, SubprocessPostgresBackend, ToolPaths,
    )

    return SubprocessPostgresBackend(
        ToolPaths(Path("pg_dump.exe"), Path("pg_restore.exe"), Path("psql.exe")),
        PostgresConnection("host", 5432, "user", "SECRET"), runner,
    )


class _Runner:
    def __init__(self):
        self.calls = []

    def run(self, argv, env, timeout_seconds, input_text=None):
        self.calls.append((argv, input_text))
        return ""


def test_i4_prepare_baseline_aplica_migraciones_faltantes_y_verifica(monkeypatch):
    runner = _Runner()
    backend = _backend(runner)
    monkeypatch.setattr(
        backend, "_missing_tables",
        lambda database, tables: list(tables) if database else list(tables),
    )
    monkeypatch.setattr(backend, "record_schema_baseline", lambda database: None)
    version = backend.prepare_schema_baseline("tenant_new")
    assert version == provision_tenant.SCHEMA_VERSION
    sql = [text for _, text in runner.calls if text]
    assert any("CREATE TABLE public.pago_ajuste" in item for item in sql)
    assert any("stripe_webhook_huerfano" in item for item in sql)
    assert "BEGIN TRANSACTION READ ONLY" in sql[-1]


def test_i4_instalacion_parcial_bloquea_el_etiquetado():
    runner = _Runner()
    backend = _backend(runner)

    def parcial(database, tables):
        return [tables[1]] if len(tables) > 1 else []

    import scripts.saas.provision_tenant as module

    original = module.CU36_SCHEMA_MARKERS
    try:
        module.CU36_SCHEMA_MARKERS = {
            "20261116000000": ("public.pago_ajuste", "public.pago_ajuste_detalle"),
        }
        backend._missing_tables = parcial  # type: ignore[method-assign]
        with pytest.raises(provision_tenant.ProvisioningError, match="partial"):
            backend.prepare_schema_baseline("tenant_new")
    finally:
        module.CU36_SCHEMA_MARKERS = original


# --------------------------------------------------------------------- I-5
def _control_rows(version):
    return [
        (i, f"EMPRESA-{i}", "ACTIVA", 100 + i, f"tenant_e_{i}", "ACTIVA", version)
        for i in range(1, 8)
    ]


@pytest.mark.parametrize("version", [
    "v3-sprint3-cu26-cu27-cu29",
    "v4-sprint3-cu32-cu33",
    "v5-sprint4-cu36",
    "v6-sprint4-cu36",
])
def test_i5_preflight_acepta_todas_las_versiones_soportadas(version):
    assert preflight_rbac_cu26_cu27.validate_control_rows(
        _control_rows(version),
    )["companies"] == 7


def test_i5_preflight_rechaza_una_version_no_soportada():
    with pytest.raises(preflight_rbac_cu26_cu27.PreflightError, match="unsupported"):
        preflight_rbac_cu26_cu27.validate_control_rows(_control_rows("v0-legacy"))


# --------------------------------------------------------------------- I-6
def test_i6_runbook_usa_la_variable_real_del_migrador():
    from scripts.saas import migrate_active_tenants

    docs = (ROOT / "docs" / "CU36_BACKEND.md").read_text(encoding="utf-8")
    assert migrate_active_tenants.ALLOW_ENV == "CU31_TENANT_MIGRATIONS_ALLOW"
    assert migrate_active_tenants.ALLOW_ENV in docs
    assert "ALLOW_TENANT_MIGRATION=" not in docs


# --------------------------------------------------------------------- I-7
def test_i7_sql_externo_coincide_con_el_manifiesto_y_usa_lf():
    bundle = load_bundle(ROOT / "database" / "tenant_migrations")
    external = bundle.external_migrations[0]
    path = ROOT / "database" / "tenant_migrations" / "versions" / (
        external.version + "_cu26_cu27_cu29.sql"
    )
    data = path.read_bytes()
    assert b"\r" not in data
    assert hashlib.sha256(data).hexdigest() == external.checksum
