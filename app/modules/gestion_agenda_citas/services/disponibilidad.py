from datetime import date, datetime, time, timedelta

from app.modules.gestion_agenda_citas.schemas.schemas import (
    DURACION_CITA_MINUTOS,
)


# =========================================================
# CU09 - CÁLCULO DE DISPONIBILIDAD
# Lógica pura e intervalos horarios (sin acceso a BD).
# =========================================================

_FECHA_REFERENCIA = date(1900, 1, 1)


def _a_datetime(valor: time) -> datetime:
    return datetime.combine(_FECHA_REFERENCIA, valor)


def obtener_dia_semana(fecha: date) -> int:
    """Convención ISO: 1 = Lunes ... 7 = Domingo."""
    return fecha.isoweekday()


def fusionar_intervalos(
    intervalos: list[tuple[time, time]],
) -> list[tuple[time, time]]:
    """Une intervalos que se solapan o son contiguos y ordena el resultado."""
    if not intervalos:
        return []

    ordenados = sorted(
        intervalos,
        key=lambda par: (_a_datetime(par[0]), _a_datetime(par[1])),
    )

    resultado: list[tuple[time, time]] = []
    inicio_actual, fin_actual = _a_datetime(ordenados[0][0]), _a_datetime(ordenados[0][1])

    for inicio, fin in ordenados[1:]:
        inicio_dt, fin_dt = _a_datetime(inicio), _a_datetime(fin)
        if inicio_dt <= fin_actual:
            if fin_dt > fin_actual:
                fin_actual = fin_dt
        else:
            resultado.append((inicio_actual.time(), fin_actual.time()))
            inicio_actual, fin_actual = inicio_dt, fin_dt

    resultado.append((inicio_actual.time(), fin_actual.time()))

    return resultado


def _restar_ocupaciones_de_base(
    base: tuple[time, time],
    ocupaciones: list[tuple[time, time]],
) -> list[tuple[time, time]]:
    """Resta ocupaciones fusionadas de un bloque base de horario."""
    inicio_base = _a_datetime(base[0])
    fin_base = _a_datetime(base[1])

    libres: list[tuple[time, time]] = []
    cursor = inicio_base

    for inicio_ocup, fin_ocup in ocupaciones:
        inicio_dt = _a_datetime(inicio_ocup)
        fin_dt = _a_datetime(fin_ocup)

        if fin_dt <= cursor:
            continue
        if inicio_dt >= fin_base:
            break

        if inicio_dt > cursor:
            libres.append((cursor.time(), min(inicio_dt, fin_base).time()))

        if fin_dt > cursor:
            cursor = fin_dt

        if cursor >= fin_base:
            break

    if cursor < fin_base:
        libres.append((cursor.time(), fin_base.time()))

    return libres


def generar_slots_disponibles(
    intervalos_libres: list[tuple[time, time]],
    duracion_minutos: int = DURACION_CITA_MINUTOS,
) -> list[tuple[time, time]]:
    """Divide cada intervalo libre en turnos fijos de `duracion_minutos`.

    Regla compartida CU09/CU10: la duración de una cita médica es fija
    (`DURACION_CITA_MINUTOS`), por lo que la disponibilidad se expone en
    turnos de esa misma duración.
    """
    if duracion_minutos <= 0:
        raise ValueError("duracion_minutos debe ser mayor a 0")

    paso = timedelta(minutes=duracion_minutos)
    slots: list[tuple[time, time]] = []

    for inicio, fin in intervalos_libres:
        cursor = _a_datetime(inicio)
        fin_dt = _a_datetime(fin)

        while cursor + paso <= fin_dt:
            slots.append((cursor.time(), (cursor + paso).time()))
            cursor += paso

    return slots


def calcular_intervalos_disponibles(
    horarios_base: list[tuple[time, time]],
    ocupaciones: list[tuple[time, time]],
    duracion_minutos: int = DURACION_CITA_MINUTOS,
) -> list[tuple[time, time]]:
    """Calcula los turnos libres reales de la jornada.

    Regla: horario base - bloqueos - citas no canceladas = intervalos libres,
    y cada intervalo libre se divide en turnos de `duracion_minutos`
    (misma regla que CU10). CU09 y CU10 comparten `DURACION_CITA_MINUTOS`.
    """
    bases_validas = [
        (inicio, fin)
        for inicio, fin in horarios_base
        if inicio < fin
    ]
    if not bases_validas:
        return []

    bases_fusionadas = fusionar_intervalos(bases_validas)

    ocupaciones_validas = [
        (inicio, fin)
        for inicio, fin in ocupaciones
        if inicio < fin
    ]
    ocupaciones_fusionadas = fusionar_intervalos(ocupaciones_validas)

    disponibles: list[tuple[time, time]] = []
    for base in bases_fusionadas:
        disponibles.extend(
            _restar_ocupaciones_de_base(base, ocupaciones_fusionadas)
        )

    disponibles.sort(key=lambda par: _a_datetime(par[0]))

    return generar_slots_disponibles(disponibles, duracion_minutos)
