"""Safe internal errors for tenant resolution and connection creation."""


class TenantError(Exception):
    """Base class whose messages intentionally contain no connection details."""


class TenantNotFoundError(TenantError):
    pass


class TenantInactiveError(TenantError):
    pass


class SubscriptionInactiveError(TenantError):
    pass


class TenantDatabaseUnavailableError(TenantError):
    pass


class InvalidTenantDatabaseNameError(TenantError):
    pass


class TenantConnectionError(TenantError):
    pass


class ControlPlaneConsistencyError(TenantError):
    pass


class TenantUserAuthenticationError(TenantError):
    """A tenant-user credential failure safe to expose generically."""
