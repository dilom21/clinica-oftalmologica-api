from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import APP_TIMEZONE
from app.modules.gestion_reportes.registry.progressive import REPORTES_PROGRESIVOS
from app.modules.gestion_reportes.repositories.progressive import obtener_citas_agregables, obtener_fechas_consulta
from app.modules.gestion_reportes.schemas.progressive import EstadoDato, FiltrosProgresivos, Granularidad

PRIVACY_K = 5


def _utc_bounds(filtros):
    zone = ZoneInfo(APP_TIMEZONE)
    start = datetime.combine(filtros.fecha_desde, time.min, zone)
    end = datetime.combine(filtros.fecha_hasta + timedelta(days=1), time.min, zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _local_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(APP_TIMEZONE)).date()


def _bucket_start(value, granularity):
    if granularity == Granularidad.DIA:
        return value
    if granularity == Granularidad.SEMANA:
        return value - timedelta(days=value.weekday())
    return value.replace(day=1)


def _next_bucket(value, granularity):
    if granularity == Granularidad.DIA:
        return value + timedelta(days=1)
    if granularity == Granularidad.SEMANA:
        return value + timedelta(days=7)
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def _protected(count):
    if count == 0:
        return {"valor": 0, "estado_dato": EstadoDato.ZERO}
    if count < PRIVACY_K:
        return {"valor": None, "estado_dato": EstadoDato.SUPPRESSED}
    return {"valor": count, "estado_dato": EstadoDato.OK}


def _load(db, filtros):
    desde_utc, hasta_utc = _utc_bounds(filtros)
    citas = obtener_citas_agregables(db, desde_utc, hasta_utc, filtros.fecha_desde, filtros.fecha_hasta)
    consultas = obtener_fechas_consulta(db, desde_utc, hasta_utc)
    return citas, consultas


def _dates(indicador, citas, consultas, filtros):
    result = []
    if indicador == "consultas_clinicas":
        result = [_local_date(item) for item in consultas]
    for created, scheduled, state, updated in citas:
        state = (state or "").upper()
        if filtros.estado and state != filtros.estado:
            continue
        if indicador == "citas_creadas":
            candidate = _local_date(created)
        elif indicador == "citas_programadas":
            candidate = scheduled
        elif indicador == "citas_atendidas" and state == "ATENDIDA":
            candidate = scheduled
        elif indicador == "cancelaciones" and state == "CANCELADA":
            candidate = scheduled
        elif indicador == "inasistencias_descriptivas" and state == "NO_ASISTIO":
            candidate = scheduled
        elif indicador == "reprogramaciones_proxy" and updated and created and updated != created:
            candidate = scheduled
        else:
            continue
        if filtros.fecha_desde <= candidate <= filtros.fecha_hasta:
            result.append(candidate)
    return result


def serie(db, indicador, filtros: FiltrosProgresivos):
    if indicador not in REPORTES_PROGRESIVOS or not REPORTES_PROGRESIVOS[indicador]["disponible"] or indicador == "calidad_cobertura":
        raise ValueError("Indicador no disponible")
    citas, consultas = _load(db, filtros)
    counts = Counter(_bucket_start(item, filtros.granularidad) for item in _dates(indicador, citas, consultas, filtros))
    cursor = _bucket_start(filtros.fecha_desde, filtros.granularidad)
    points = []
    while cursor <= filtros.fecha_hasta:
        following = _next_bucket(cursor, filtros.granularidad)
        points.append({
            "periodo_inicio": max(cursor, filtros.fecha_desde),
            "periodo_fin": min(following - timedelta(days=1), filtros.fecha_hasta),
            **_protected(counts[cursor]),
        })
        cursor = following
    metadata = REPORTES_PROGRESIVOS[indicador]
    return {
        "indicador": indicador, "etiqueta": metadata["etiqueta"],
        "granularidad": filtros.granularidad, "fecha_desde": filtros.fecha_desde,
        "fecha_hasta": filtros.fecha_hasta, "zona_horaria": APP_TIMEZONE,
        "puntos": points, "advertencias": metadata["limitaciones"],
    }


def kpis(db, filtros: FiltrosProgresivos):
    citas, consultas = _load(db, filtros)
    keys = ("citas_creadas", "citas_programadas", "citas_atendidas", "cancelaciones", "reprogramaciones_proxy", "inasistencias_descriptivas", "consultas_clinicas")
    items = []
    for key in keys:
        metadata = REPORTES_PROGRESIVOS[key]
        items.append({"indicador": key, "etiqueta": metadata["etiqueta"], "calificador": metadata["calificador"],
                      "advertencias": metadata["limitaciones"], **_protected(len(_dates(key, citas, consultas, filtros)))})
    distribution = []
    for state in ("PROGRAMADA", "CONFIRMADA", "EN_ESPERA", "ATENDIDA", "CANCELADA", "NO_ASISTIO"):
        count = sum(1 for _, scheduled, current, _ in citas if filtros.fecha_desde <= scheduled <= filtros.fecha_hasta and (current or "").upper() == state)
        distribution.append({"indicador": state, "etiqueta": state.replace("_", " ").title(), "calificador": None, "advertencias": [], **_protected(count)})
    return {"fecha_desde": filtros.fecha_desde, "fecha_hasta": filtros.fecha_hasta, "zona_horaria": APP_TIMEZONE,
            "indicadores": items, "distribucion_estados": distribution,
            "advertencias": [f"Supresión primaria k={PRIVACY_K} por agregado.", "La cobertura productiva del tenant no ha sido perfilada; ZERO solo representa el rango consultado."]}


def comparar(db, indicador, filtros):
    if indicador not in REPORTES_PROGRESIVOS or not REPORTES_PROGRESIVOS[indicador]["disponible"] or indicador == "calidad_cobertura":
        raise ValueError("Indicador no disponible")
    current_dates = _dates(indicador, *_load(db, filtros), filtros)
    days = (filtros.fecha_hasta - filtros.fecha_desde).days + 1
    previous = filtros.model_copy(update={"fecha_desde": filtros.fecha_desde - timedelta(days=days), "fecha_hasta": filtros.fecha_desde - timedelta(days=1)})
    previous_dates = _dates(indicador, *_load(db, previous), previous)
    current, old = _protected(len(current_dates)), _protected(len(previous_dates))
    absolute = percentage = None
    if current["valor"] is not None and old["valor"] is not None:
        absolute = current["valor"] - old["valor"]
        percentage = None if old["valor"] == 0 else round(absolute * 100 / old["valor"], 2)
    return {"indicador": indicador, "actual": current, "anterior": old, "variacion_absoluta": absolute,
            "variacion_porcentual": percentage, "periodo_actual": (filtros.fecha_desde, filtros.fecha_hasta),
            "periodo_anterior": (previous.fecha_desde, previous.fecha_hasta),
            "advertencias": REPORTES_PROGRESIVOS[indicador]["limitaciones"]}
