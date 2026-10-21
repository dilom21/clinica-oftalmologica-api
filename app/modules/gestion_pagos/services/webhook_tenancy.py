"""Fail-closed database routing for signed Stripe webhook events.

No clinical session is opened until Stripe has verified the event signature.
Tenant routing accepts only server-generated metadata from that signed event;
database names and request headers are never routing inputs.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.tenancy.context import TenantContext
from app.core.tenancy.exceptions import (
    SubscriptionInactiveError,
    TenantError,
    TenantInactiveError,
    TenantNotFoundError,
)
from app.core.tenancy.registry_provider import get_tenant_engine_registry
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.database import session as database_session


TENANT_SCOPE_VALUE = "tenant"
TENANT_ROUTING_KEYS = frozenset(
    {"clinica_contexto", "empresa_id", "empresa_codigo", "tenant_id"}
)
FORBIDDEN_ROUTING_KEYS = frozenset(
    {"database", "database_name", "db", "db_name", "connection_string"}
)


@dataclass(frozen=True, slots=True)
class StripeWebhookScope:
    """Routing identity extracted exclusively from a verified Stripe event."""

    is_tenant: bool
    metadata: dict[str, str]
    tenant_context: TenantContext | None = None

    @property
    def pago_id(self) -> int | None:
        return _optional_positive_int(self.metadata, "pago_id")

    @property
    def consulta_clinica_id(self) -> int | None:
        return _optional_positive_int(self.metadata, "consulta_clinica_id")


def _metadata_inconsistente() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="La metadata Stripe no identifica un pago de forma consistente",
    )


def _metadata_del_evento(evento: dict[str, Any]) -> dict[str, str]:
    objeto = evento.get("data", {}).get("object", {})
    metadata = objeto.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise _metadata_inconsistente()
    if any(key in metadata for key in FORBIDDEN_ROUTING_KEYS):
        raise _metadata_inconsistente()
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in metadata.items()
    ):
        raise _metadata_inconsistente()
    return metadata


def _positive_int(metadata: dict[str, str], key: str) -> int:
    try:
        value = int(metadata[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise _metadata_inconsistente() from exc
    if value <= 0 or str(value) != metadata[key]:
        raise _metadata_inconsistente()
    return value


def _optional_positive_int(metadata: dict[str, str], key: str) -> int | None:
    if key not in metadata:
        return None
    return _positive_int(metadata, key)


class StripeWebhookSessionResolver:
    """Resolve a verified Stripe event to exactly one clinical database."""

    def __init__(
        self,
        *,
        registry,
        control_session_factory: Callable[[], Session],
        legacy_session_factory: Callable[[], Session],
    ):
        self._registry = registry
        self._control_session_factory = control_session_factory
        self._legacy_session_factory = legacy_session_factory

    def _resolve_scope(self, evento: dict[str, Any]) -> StripeWebhookScope:
        metadata = _metadata_del_evento(evento)
        present = TENANT_ROUTING_KEYS.intersection(metadata)
        if not present:
            # Old PaymentIntents without tenant markers remain legacy. They
            # are never guessed as belonging to a tenant.
            return StripeWebhookScope(is_tenant=False, metadata=metadata)
        if present != TENANT_ROUTING_KEYS:
            raise _metadata_inconsistente()
        if metadata["clinica_contexto"] != TENANT_SCOPE_VALUE:
            raise _metadata_inconsistente()
        if not metadata.get("empresa_codigo"):
            raise _metadata_inconsistente()
        empresa_id = _positive_int(metadata, "empresa_id")
        tenant_id = _positive_int(metadata, "tenant_id")
        # New tenant events always bind payment and consultation as well.
        _positive_int(metadata, "pago_id")
        _positive_int(metadata, "consulta_clinica_id")

        control = None
        try:
            control = self._control_session_factory()
            resolver = TenantResolver(TenantControlPlaneRepository(control))
            context = resolver.resolver_por_codigo_empresa(
                metadata["empresa_codigo"]
            )
            resolver.require_active_database(context)
        except (
            TenantNotFoundError,
            TenantInactiveError,
            SubscriptionInactiveError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El tenant del evento Stripe no esta habilitado",
            ) from exc
        except TenantError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="El tenant del evento Stripe no esta disponible",
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No se pudo resolver el tenant del evento Stripe",
            ) from exc
        finally:
            if control is not None:
                control.close()

        if (
            context.empresa_id != empresa_id
            or context.tenant_database_id != tenant_id
            or context.empresa_codigo != metadata["empresa_codigo"]
        ):
            raise _metadata_inconsistente()
        return StripeWebhookScope(
            is_tenant=True,
            metadata=metadata,
            tenant_context=context,
        )

    @contextmanager
    def open_for_verified_event(
        self,
        evento: dict[str, Any],
    ) -> Iterator[tuple[Session, StripeWebhookScope]]:
        scope = self._resolve_scope(evento)
        try:
            if scope.is_tenant:
                db = self._registry.get_session(scope.tenant_context)
                db.info["tenant_context"] = scope.tenant_context
            else:
                db = self._legacy_session_factory()
                db.info["tenant_context"] = None
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="La base clinica del evento Stripe no esta disponible",
            ) from exc
        try:
            yield db, scope
        finally:
            db.close()


def get_stripe_webhook_session_resolver(
    registry=Depends(get_tenant_engine_registry),
) -> StripeWebhookSessionResolver:
    """Construct the resolver without opening any database session."""
    return StripeWebhookSessionResolver(
        registry=registry,
        control_session_factory=database_session.open_control_session,
        legacy_session_factory=database_session.SessionLocal,
    )
