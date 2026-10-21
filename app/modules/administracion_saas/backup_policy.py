"""Pure, ORM-agnostic backup policy and occurrence engine.

``policy`` is any object exposing ``frecuencia``, ``hora_local`` (datetime.time)
and ``timezone`` (str). All computations are timezone aware and converted to UTC.
"""

from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FREQUENCIES = ("DIARIA", "SEMANAL", "MENSUAL")
WINDOW_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class PolicyError(ValueError):
    """Invalid or uncomputable backup policy."""


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def zone_for(name):
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise PolicyError("Backup policy timezone is invalid") from exc


def period_start(frequency, local_date):
    if frequency == "DIARIA":
        return local_date
    if frequency == "SEMANAL":
        return local_date - timedelta(days=local_date.weekday())
    if frequency == "MENSUAL":
        return local_date.replace(day=1)
    raise PolicyError("Backup policy frequency is unsupported")


def step_period(frequency, start, count):
    if frequency == "DIARIA":
        return start + timedelta(days=count)
    if frequency == "SEMANAL":
        return start + timedelta(weeks=count)
    if frequency == "MENSUAL":
        month = start.month - 1 + count
        return start.replace(year=start.year + month // 12, month=month % 12 + 1, day=1)
    raise PolicyError("Backup policy frequency is unsupported")


def occurrence(policy, local_date):
    zone = zone_for(policy.timezone)
    naive = datetime.combine(local_date, policy.hora_local)
    return naive.replace(tzinfo=zone).astimezone(timezone.utc)


def next_occurrence(policy, after_utc):
    after_utc = as_utc(after_utc)
    start = period_start(policy.frecuencia, after_utc.astimezone(zone_for(policy.timezone)).date())
    for index in range(0, 5):
        candidate = occurrence(policy, step_period(policy.frecuencia, start, index))
        if candidate > after_utc:
            return candidate
    raise PolicyError("Backup policy produced no occurrence")


def window_start(policy, now_utc):
    now_utc = as_utc(now_utc)
    start = period_start(policy.frecuencia, now_utc.astimezone(zone_for(policy.timezone)).date())
    previous = None
    for index in range(-1, 4):
        candidate = occurrence(policy, step_period(policy.frecuencia, start, index))
        if candidate <= now_utc:
            previous = candidate
        else:
            break
    if previous is None:
        raise PolicyError("Backup policy window could not be computed")
    return previous


def window_key(occurrence_utc):
    return as_utc(occurrence_utc).strftime(WINDOW_FORMAT)
