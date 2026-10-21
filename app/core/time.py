"""Utilities for application-local dates."""

from datetime import date, datetime, timezone

from app.core import config


def fecha_local_aplicacion() -> date:
    """Returns today's date in the validated application timezone."""
    zona = config.ZoneInfo(config.APP_TIMEZONE)
    return datetime.now(zona).date()


def fecha_hora_utc(_context=None) -> datetime:
    """Returns the current timezone-aware UTC date and time."""
    return datetime.now(timezone.utc)
