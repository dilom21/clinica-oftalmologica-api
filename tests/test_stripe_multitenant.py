from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.tenancy.context import TenantContext
from app.core.tenancy.exceptions import TenantInactiveError
from app.main import app
from app.modules.gestion_pagos.models.models import Pago, PagoDetalle
from app.modules.gestion_pagos.services import service
from app.modules.gestion_pagos.services import webhook_tenancy
from app.modules.gestion_pagos.services.webhook_tenancy import (
    StripeWebhookScope,
    StripeWebhookSessionResolver,
    get_stripe_webhook_session_resolver,
)
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from tests.test_base_flujo_pagos import cliente_pagos, db_pagos
from tests.test_stripe_pagos import (
    cliente_stripe,
    evento_stripe,
    proveedor_falso,
)


ROOT = Path(__file__).resolve().parents[1]


def tenant_context(empresa_id, codigo, tenant_id, database_name):
    return TenantContext(
        empresa_id=empresa_id,
        empresa_codigo=codigo,
        empresa_slug=codigo.lower(),
        empresa_estado="ACTIVA",
        suscripcion_id=empresa_id,
        plan_codigo="PRO",
        suscripcion_estado="ACTIVA",
        suscripcion_fecha_inicio=date(2026, 1, 1),
        suscripcion_fecha_fin=date(2027, 1, 1),
        tenant_database_id=tenant_id,
        database_name=database_name,
        database_estado="ACTIVA",
        version_schema="1",
    )


@pytest.fixture
def dos_bases_tenant(db_pagos):
    engine_b = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    source = db_pagos.connection().connection.driver_connection
    destination = engine_b.raw_connection()
    try:
        source.backup(destination.driver_connection)
        destination.commit()
    finally:
        destination.close()
    db_b = Session(engine_b, autoflush=False)
    try:
        yield db_pagos, db_b
    finally:
        db_b.close()
        engine_b.dispose()


def limpiar(db):
    db.execute(delete(PagoDetalle))
    db.execute(delete(Pago))
    db.commit()


def test_dos_tenants_con_pago_id_1_generan_metadata_y_claves_distintas(
    dos_bases_tenant,
    proveedor_falso,
):
    db_a, db_b = dos_bases_tenant
    contexto_a = tenant_context(10, "CLINICA_A", 101, "clinica_a")
    contexto_b = tenant_context(20, "CLINICA_B", 202, "clinica_b")
    resultados = []
    for db, contexto in ((db_a, contexto_a), (db_b, contexto_b)):
        limpiar(db)
        db.info["tenant_context"] = contexto
        respuesta = service.crear_intencion_stripe(
            db, [1000], db.get(Usuario, 11), proveedor_falso
        )
        resultados.append(respuesta)

    assert [item.pago_id for item in resultados] == [1, 1]
    primera, segunda = proveedor_falso.creaciones
    assert primera["idempotency_key"] == "clinica-tenant-101-pago-1"
    assert segunda["idempotency_key"] == "clinica-tenant-202-pago-1"
    assert primera["metadata"] == {
        "clinica_contexto": "tenant",
        "empresa_id": "10",
        "empresa_codigo": "CLINICA_A",
        "tenant_id": "101",
        "pago_id": "1",
        "consulta_clinica_id": "100",
    }
    assert segunda["metadata"]["empresa_codigo"] == "CLINICA_B"
    assert "database_name" not in primera["metadata"]
    assert "database_name" not in segunda["metadata"]


class ResolverDosBases:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = 0

    @contextmanager
    def open_for_verified_event(self, evento):
        self.calls += 1
        metadata = evento["data"]["object"]["metadata"]
        db, contexto = self.mapping[metadata["empresa_codigo"]]
        yield db, StripeWebhookScope(True, metadata, contexto)


def _metadata(contexto, *, pago_id="1", consulta_id="100", **extra):
    return {
        "clinica_contexto": "tenant",
        "empresa_id": str(contexto.empresa_id),
        "empresa_codigo": contexto.empresa_codigo,
        "tenant_id": str(contexto.tenant_database_id),
        "pago_id": pago_id,
        "consulta_clinica_id": consulta_id,
        **extra,
    }


def test_webhooks_tenant_actualizan_solo_su_empresa_y_son_idempotentes(
    cliente_stripe,
    proveedor_falso,
    dos_bases_tenant,
):
    db_a, db_b = dos_bases_tenant
    contexto_a = tenant_context(10, "CLINICA_A", 101, "clinica_a")
    contexto_b = tenant_context(20, "CLINICA_B", 202, "clinica_b")
    for db, referencia in ((db_a, "pi_compartido"), (db_b, "pi_compartido")):
        limpiar(db)
        from tests.test_stripe_pagos import insertar_pago_stripe

        insertar_pago_stripe(db, pago_id=1, referencia=referencia)

    resolver = ResolverDosBases(
        {"CLINICA_A": (db_a, contexto_a), "CLINICA_B": (db_b, contexto_b)}
    )
    app.dependency_overrides[get_stripe_webhook_session_resolver] = lambda: resolver

    proveedor_falso.evento = evento_stripe(
        "payment_intent.succeeded",
        intento="pi_compartido",
        metadata=_metadata(contexto_a),
    )
    assert cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw-a",
        headers={"Stripe-Signature": "valida"},
    ).status_code == 200
    db_a.expire_all()
    db_b.expire_all()
    assert db_a.get(Pago, 1).estado == "APROBADO"
    assert db_b.get(Pago, 1).estado == "PENDIENTE"

    proveedor_falso.evento = evento_stripe(
        "payment_intent.succeeded",
        intento="pi_compartido",
        metadata=_metadata(contexto_b),
    )
    assert cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw-b",
        headers={"Stripe-Signature": "valida"},
    ).status_code == 200
    # Repetir el mismo evento no degrada ni duplica la aprobacion.
    assert cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw-b",
        headers={"Stripe-Signature": "valida"},
    ).status_code == 200
    db_b.expire_all()
    assert db_b.get(Pago, 1).estado == "APROBADO"


def test_webhook_rechaza_metadata_de_pago_inconsistente(
    cliente_stripe,
    proveedor_falso,
    dos_bases_tenant,
):
    db_a, _ = dos_bases_tenant
    contexto = tenant_context(10, "CLINICA_A", 101, "clinica_a")
    limpiar(db_a)
    from tests.test_stripe_pagos import insertar_pago_stripe

    insertar_pago_stripe(db_a, pago_id=1, referencia="pi_a")
    resolver = ResolverDosBases({"CLINICA_A": (db_a, contexto)})
    app.dependency_overrides[get_stripe_webhook_session_resolver] = lambda: resolver
    proveedor_falso.evento = evento_stripe(
        "payment_intent.succeeded",
        intento="pi_a",
        metadata=_metadata(contexto, pago_id="2"),
    )
    respuesta = cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw",
        headers={"Stripe-Signature": "valida"},
    )
    assert respuesta.status_code == 409
    db_a.expire_all()
    assert db_a.get(Pago, 1).estado == "PENDIENTE"


@pytest.mark.parametrize(
    "metadata",
    [
        {"clinica_contexto": "tenant", "empresa_id": "1"},
        {
            "clinica_contexto": "tenant",
            "empresa_id": "1",
            "empresa_codigo": "A",
            "tenant_id": "1",
            "pago_id": "1",
            "consulta_clinica_id": "1",
            "database_name": "base_elegida_por_cliente",
        },
    ],
)
def test_resolver_rechaza_metadata_parcial_o_seleccion_arbitraria_de_bd(metadata):
    resolver = StripeWebhookSessionResolver(
        registry=None,
        control_session_factory=lambda: pytest.fail("no debe abrir Control Plane"),
        legacy_session_factory=lambda: pytest.fail("no debe caer en legacy"),
    )
    evento = evento_stripe(
        "payment_intent.succeeded", metadata=metadata
    )
    with pytest.raises(HTTPException) as error:
        with resolver.open_for_verified_event(evento):
            pass
    assert error.value.status_code == 409


def test_tenant_suspendido_falla_sin_abrir_legacy(monkeypatch):
    class Control:
        def close(self):
            pass

    class ResolverSuspendido:
        def __init__(self, repository):
            pass

        def resolver_por_codigo_empresa(self, codigo):
            raise TenantInactiveError("suspendido")

    monkeypatch.setattr(webhook_tenancy, "TenantResolver", ResolverSuspendido)
    contexto = tenant_context(10, "CLINICA_A", 101, "clinica_a")
    resolver = StripeWebhookSessionResolver(
        registry=None,
        control_session_factory=Control,
        legacy_session_factory=lambda: pytest.fail("no debe caer en legacy"),
    )
    with pytest.raises(HTTPException) as error:
        with resolver.open_for_verified_event(
            evento_stripe(
                "payment_intent.succeeded", metadata=_metadata(contexto)
            )
        ):
            pass
    assert error.value.status_code == 409


def test_conexion_tenant_no_disponible_falla_sin_caer_en_legacy(monkeypatch):
    class Control:
        def close(self):
            pass

    contexto = tenant_context(10, "CLINICA_A", 101, "clinica_a")

    class ResolverActivo:
        def __init__(self, repository):
            pass

        def resolver_por_codigo_empresa(self, codigo):
            return contexto

        def require_active_database(self, context):
            pass

    class RegistryCaido:
        def get_session(self, context):
            raise OSError("conexion simulada no disponible")

    monkeypatch.setattr(webhook_tenancy, "TenantResolver", ResolverActivo)
    resolver = StripeWebhookSessionResolver(
        registry=RegistryCaido(),
        control_session_factory=Control,
        legacy_session_factory=lambda: pytest.fail("no debe caer en legacy"),
    )
    with pytest.raises(HTTPException) as error:
        with resolver.open_for_verified_event(
            evento_stripe(
                "payment_intent.succeeded", metadata=_metadata(contexto)
            )
        ):
            pass
    assert error.value.status_code == 503


def test_firma_invalida_no_intenta_resolver_ninguna_base(
    cliente_stripe,
    proveedor_falso,
):
    class ResolverNoInvocable:
        @contextmanager
        def open_for_verified_event(self, evento):
            pytest.fail("la firma debe verificarse antes de abrir una base")
            yield

    app.dependency_overrides[get_stripe_webhook_session_resolver] = (
        ResolverNoInvocable
    )
    proveedor_falso.firma_valida = False
    respuesta = cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"alterado",
        headers={"Stripe-Signature": "invalida"},
    )
    assert respuesta.status_code == 400


def test_migraciones_cu22_tienen_contrato_seguro_y_separado():
    actual = (ROOT / "db" / "cu22_servicios_realizados.sql").read_text(
        encoding="utf-8"
    )
    legacy = (ROOT / "db" / "cu22_migrar_catalogo_legacy.sql").read_text(
        encoding="utf-8"
    )
    lower = actual.lower()
    legacy_lower = legacy.lower()

    assert "begin;" in lower and "commit;" in lower
    assert "add column if not exists precio_aplicado numeric(10,2)" in lower
    assert "is_nullable = 'yes'" in lower
    assert "'nan'::numeric" in lower
    assert "registrar servicios realizados" in lower
    assert "oftalmologo" in lower and "ambas" in lower
    assert "pago_detalle" in lower and "servicio_realizado" in lower
    assert "ejecutar primero db/cu22_migrar_catalogo_legacy.sql" in lower
    assert "create table" not in lower
    assert "drop table" not in lower
    assert "truncate" not in lower

    assert "to_regclass('public.servicios_oftalmologicos')" in legacy_lower
    assert "overriding system value" in legacy_lower
    assert "on conflict (id) do nothing" in legacy_lower
    assert "setval" in legacy_lower and "last_value" in legacy_lower
    assert "is distinct from" in legacy_lower
    assert "drop table" not in legacy_lower
    assert "delete from" not in legacy_lower
    assert "truncate" not in legacy_lower


def test_orm_cu22_usa_solo_nombres_canonicos():
    from app.database.base import Base

    assert "servicio_oftalmologico" in Base.metadata.tables
    assert "servicio_realizado" in Base.metadata.tables
    assert "servicios_oftalmologicos" not in Base.metadata.tables
