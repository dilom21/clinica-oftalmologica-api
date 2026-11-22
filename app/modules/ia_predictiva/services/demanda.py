from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import sqrt
from statistics import mean
from typing import Callable, Iterable, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core import config
from app.modules.ia_predictiva.repositories import listar_fechas_registro_citas

EstadoCobertura = Literal["OK", "ZERO", "SUPPRESSED", "PARTIAL", "NOT_COVERED"]
MIN_SEMANAS_VALIDAS = 52
MIN_ENTRENAMIENTO_BACKTEST = 26
MIN_PUNTOS_BACKTEST = 12


@dataclass(frozen=True)
class SemanaHistorica:
    inicio: date
    valor: int | None
    estado: EstadoCobertura


def _lunes(fecha: date) -> date:
    return fecha - timedelta(days=fecha.isoweekday() - 1)


def _como_local(valor: datetime, zona: ZoneInfo) -> datetime:
    # PostgreSQL devuelve aware; SQLite puede perder tzinfo. Las fechas de
    # registro del dominio se escriben en UTC, por lo que ese es el fallback.
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(zona)


def construir_serie_semanal(
    fechas_registro: Iterable[datetime],
    *,
    fecha_corte: datetime,
    zona: ZoneInfo,
    estados_especiales: dict[date, EstadoCobertura] | None = None,
) -> list[SemanaHistorica]:
    """Construye semanas ISO cerradas; los límites sin cobertura son parciales."""
    corte_local = _como_local(fecha_corte, zona)
    inicio_semana_corte = _lunes(corte_local.date())
    locales = sorted(
        local for valor in fechas_registro
        if (local := _como_local(valor, zona)) < corte_local
    )
    if not locales:
        return []

    conteos = Counter(_lunes(valor.date()) for valor in locales)
    primera_semana = _lunes(locales[0].date())
    ultima_semana_cerrada = inicio_semana_corte - timedelta(days=7)
    if primera_semana > ultima_semana_cerrada:
        return [SemanaHistorica(primera_semana, None, "PARTIAL")]

    especiales = estados_especiales or {}
    resultado: list[SemanaHistorica] = []
    actual = primera_semana
    while actual <= ultima_semana_cerrada:
        estado = especiales.get(actual)
        if estado is None:
            # No se conoce cobertura anterior al primer registro dentro de la
            # semana. Solo semanas posteriores completas pueden ser ZERO.
            inicio_local = datetime.combine(actual, time.min, tzinfo=zona)
            if actual == primera_semana and locales[0] > inicio_local:
                estado = "PARTIAL"
            else:
                estado = "OK" if conteos[actual] else "ZERO"
        valor = conteos[actual] if estado in {"OK", "ZERO"} else None
        resultado.append(SemanaHistorica(actual, valor, estado))
        actual += timedelta(days=7)
    return resultado


def calcular_metricas(reales: list[float], predicciones: list[float]) -> dict:
    if not reales or len(reales) != len(predicciones):
        raise ValueError("Las métricas requieren pares reales/pronosticados")
    errores = [pred - real for real, pred in zip(reales, predicciones)]
    absolutos = [abs(error) for error in errores]
    denominador = sum(abs(real) for real in reales)
    return {
        "mae": round(mean(absolutos), 4),
        "rmse": round(sqrt(mean([error * error for error in errores])), 4),
        "wape": round(sum(absolutos) / denominador, 4) if denominador else None,
        "sesgo": round(mean(errores), 4),
        "observaciones": len(reales),
    }


def _naive(historia: list[float], horizonte: int) -> list[float]:
    return [historia[-1]] * horizonte


def _promedio_movil(historia: list[float], horizonte: int) -> list[float]:
    ventana = historia[-min(8, len(historia)):]
    return [mean(ventana)] * horizonte


def _suavizado(historia: list[float], horizonte: int) -> list[float]:
    nivel = historia[0]
    for valor in historia[1:]:
        nivel = 0.35 * valor + 0.65 * nivel
    return [nivel] * horizonte


def _tendencia_lineal(historia: list[float], horizonte: int) -> list[float]:
    ventana = historia[-min(52, len(historia)):]
    n = len(ventana)
    centro_x = (n - 1) / 2
    centro_y = mean(ventana)
    denominador = sum((x - centro_x) ** 2 for x in range(n))
    pendiente = (
        sum((x - centro_x) * (y - centro_y) for x, y in enumerate(ventana))
        / denominador if denominador else 0.0
    )
    intercepto = centro_y - pendiente * centro_x
    return [max(0.0, intercepto + pendiente * (n + paso)) for paso in range(horizonte)]


def _estacional(historia: list[float], horizonte: int) -> list[float]:
    return [historia[-52 + (paso % 52)] for paso in range(horizonte)]


def ejecutar_backtesting(
    valores: list[float],
    predictor: Callable[[list[float], int], list[float]],
) -> tuple[dict, list[float]]:
    inicio = max(MIN_ENTRENAMIENTO_BACKTEST, len(valores) - 26)
    reales: list[float] = []
    predicciones: list[float] = []
    for indice in range(inicio, len(valores)):
        reales.append(valores[indice])
        predicciones.append(max(0.0, predictor(valores[:indice], 1)[0]))
    if len(reales) < MIN_PUNTOS_BACKTEST:
        raise ValueError("No hay suficientes cortes temporales para backtesting")
    return calcular_metricas(reales, predicciones), [r - p for r, p in zip(reales, predicciones)]


def _percentil(valores: list[float], proporcion: float) -> float:
    ordenados = sorted(valores)
    posicion = (len(ordenados) - 1) * proporcion
    inferior = int(posicion)
    superior = min(inferior + 1, len(ordenados) - 1)
    fraccion = posicion - inferior
    return ordenados[inferior] * (1 - fraccion) + ordenados[superior] * fraccion


def _respuesta_base(estado: str, corte: datetime, horizonte: int, serie: list[SemanaHistorica]) -> dict:
    conteos = Counter(item.estado.lower() for item in serie)
    return {
        "estado": estado,
        "zona_horaria": config.APP_TIMEZONE,
        "fecha_corte": corte,
        "horizonte_semanas": horizonte,
        "rango_historico_inicio": serie[0].inicio if serie else None,
        "rango_historico_fin": serie[-1].inicio + timedelta(days=6) if serie else None,
        "calidad_historica": {
            "semanas_ok": conteos["ok"], "semanas_zero": conteos["zero"],
            "semanas_suppressed": conteos["suppressed"],
            "semanas_partial": conteos["partial"],
            "semanas_not_covered": conteos["not_covered"],
        },
    }


def pronosticar_serie(
    serie: list[SemanaHistorica], *, fecha_corte: datetime, horizonte: int
) -> dict:
    base = _respuesta_base("INSUFFICIENT_HISTORY", fecha_corte, horizonte, serie)
    # Los límites de cobertura se informan, pero pueden recortarse. Un hueco
    # inválido dentro del rango entrenable, en cambio, bloquea el modelo.
    entrenable = list(serie)
    while entrenable and entrenable[0].estado in {"PARTIAL", "NOT_COVERED"}:
        entrenable.pop(0)
    while entrenable and entrenable[-1].estado in {"PARTIAL", "NOT_COVERED"}:
        entrenable.pop()
    if entrenable:
        base["rango_historico_inicio"] = entrenable[0].inicio
        base["rango_historico_fin"] = entrenable[-1].inicio + timedelta(days=6)
    invalidas = [s for s in entrenable if s.estado not in {"OK", "ZERO"}]
    if invalidas:
        base["estado"] = "INSUFFICIENT_DATA"
        base["advertencias"] = [
            "La serie contiene semanas SUPPRESSED, PARTIAL o NOT_COVERED; no se imputaron como cero."
        ]
        return base
    if len(entrenable) < MIN_SEMANAS_VALIDAS:
        base["advertencias"] = [f"Se requieren {MIN_SEMANAS_VALIDAS} semanas completas y válidas."]
        return base

    valores = [float(s.valor or 0) for s in entrenable]
    metodos: dict[str, Callable[[list[float], int], list[float]]] = {
        "NAIVE": _naive,
        "MOVING_AVERAGE_8": _promedio_movil,
        "EXPONENTIAL_SMOOTHING_0_35": _suavizado,
        "LINEAR_TREND_52": _tendencia_lineal,
    }
    if len(valores) >= 104:
        metodos["SEASONAL_NAIVE_52"] = _estacional

    evaluados = []
    residuos_por_metodo = {}
    try:
        for nombre, predictor in metodos.items():
            metricas, residuos = ejecutar_backtesting(valores, predictor)
            evaluados.append({"metodo": nombre, "metricas": metricas})
            residuos_por_metodo[nombre] = residuos
    except (ArithmeticError, ValueError) as exc:
        base["estado"] = "FORECAST_UNAVAILABLE"
        base["advertencias"] = [f"No fue posible validar los métodos estadísticos: {exc}"]
        return base
    evaluados.sort(key=lambda item: (item["metricas"]["mae"], item["metricas"]["rmse"], item["metodo"]))
    elegido = evaluados[0]
    predictor = metodos[elegido["metodo"]]
    predicciones = predictor(valores, horizonte)
    residuos = residuos_por_metodo[elegido["metodo"]]
    error_bajo, error_alto = _percentil(residuos, 0.10), _percentil(residuos, 0.90)
    siguiente = entrenable[-1].inicio + timedelta(days=7)
    base.update({
        "estado": "READY", "metodo": elegido["metodo"],
        "metricas": elegido["metricas"],
        "metricas_baseline": next(item["metricas"] for item in evaluados if item["metodo"] == "NAIVE"),
        "comparacion_metodos": evaluados,
        "intervalo": "Empírico central 80% de residuos de backtesting; no es una garantía estadística.",
        "pronosticos": [
            {
                "inicio_semana": siguiente + timedelta(days=7 * paso),
                "fin_semana": siguiente + timedelta(days=7 * paso + 6),
                "valor_pronosticado": round(max(0.0, valor), 2),
                "limite_inferior": round(max(0.0, valor + error_bajo), 2),
                "limite_superior": round(max(0.0, valor + error_alto), 2),
            }
            for paso, valor in enumerate(predicciones)
        ],
        "advertencias": [],
    })
    baseline = base["metricas_baseline"]
    if elegido["metodo"] == "NAIVE" or elegido["metricas"]["mae"] >= baseline["mae"]:
        base["advertencias"].append("Ningún método demostró mejora de MAE frente al baseline ingenuo.")
    return base


def generar_pronostico_semanal(
    db: Session,
    *,
    horizonte: int,
    fecha_corte: datetime | None = None,
) -> dict:
    zona = ZoneInfo(config.APP_TIMEZONE)
    corte = fecha_corte or datetime.now(zona)
    if corte.tzinfo is None:
        corte = corte.replace(tzinfo=zona)
    corte_utc = corte.astimezone(timezone.utc)
    fechas = listar_fechas_registro_citas(db, antes_de=corte_utc)
    serie = construir_serie_semanal(fechas, fecha_corte=corte, zona=zona)
    return pronosticar_serie(serie, fecha_corte=corte, horizonte=horizonte)
