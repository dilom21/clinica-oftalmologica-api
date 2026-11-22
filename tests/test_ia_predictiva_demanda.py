from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.database import session as database_session
from app.main import app
from app.modules.ia_predictiva.api.router import (
    obtener_administrador_actual as dependencia_administrador,
    permiso_pronostico,
)
from app.modules.ia_predictiva.services.demanda import (
    SemanaHistorica,
    calcular_metricas,
    construir_serie_semanal,
    ejecutar_backtesting,
    pronosticar_serie,
)


def _serie(valores, inicio=date(2025, 1, 6)):
    return [
        SemanaHistorica(inicio + timedelta(days=7 * i), valor, "ZERO" if valor == 0 else "OK")
        for i, valor in enumerate(valores)
    ]


def _corte(serie):
    return datetime.combine(serie[-1].inicio + timedelta(days=7), datetime.min.time(), timezone.utc)


@pytest.mark.parametrize("valores", [
    [10] * 60,
    list(range(10, 70)),
    list(range(80, 20, -1)),
    [0 if i % 9 == 0 else 12 for i in range(60)],
])
def test_pronostica_series_estable_tendencias_y_ceros(valores):
    serie = _serie(valores)
    resultado = pronosticar_serie(serie, fecha_corte=_corte(serie), horizonte=6)
    assert resultado["estado"] == "READY"
    assert len(resultado["pronosticos"]) == 6
    assert resultado["metricas"]["observaciones"] >= 12
    assert all(item["limite_inferior"] >= 0 for item in resultado["pronosticos"])
    if valores == list(range(10, 70)):
        assert resultado["pronosticos"][-1]["valor_pronosticado"] >= resultado["pronosticos"][0]["valor_pronosticado"]


def test_historial_menor_a_52_es_insuficiente():
    serie = _serie([5] * 51)
    resultado = pronosticar_serie(serie, fecha_corte=_corte(serie), horizonte=4)
    assert resultado["estado"] == "INSUFFICIENT_HISTORY"
    assert resultado["pronosticos"] == [] if "pronosticos" in resultado else True


@pytest.mark.parametrize("estado", ["SUPPRESSED", "PARTIAL", "NOT_COVERED"])
def test_semana_no_valida_no_se_imputa_como_cero(estado):
    serie = _serie([8] * 60)
    serie[20] = SemanaHistorica(serie[20].inicio, None, estado)
    resultado = pronosticar_serie(serie, fecha_corte=_corte(serie), horizonte=4)
    assert resultado["estado"] == "INSUFFICIENT_DATA"
    assert resultado["calidad_historica"][f"semanas_{estado.lower()}"] == 1


def test_iso_timezone_y_cambio_de_anio():
    zona = ZoneInfo("America/La_Paz")
    # 00:30 UTC del lunes todavía pertenece al domingo local y a la semana ISO anterior.
    fechas = [
        datetime(2026, 1, 5, 0, 30, tzinfo=timezone.utc),
        datetime(2026, 1, 5, 5, 0, tzinfo=timezone.utc),
    ]
    serie = construir_serie_semanal(
        fechas,
        fecha_corte=datetime(2026, 1, 12, 4, tzinfo=timezone.utc),
        zona=zona,
    )
    assert serie[0].inicio == date(2025, 12, 29)
    assert serie[-1].inicio == date(2026, 1, 5)
    assert [s.valor for s in serie] == [None, 1]
    assert serie[0].estado == "PARTIAL"


def test_backtesting_entrena_solo_con_el_pasado():
    longitudes = []

    def predictor(historia, horizonte):
        longitudes.append(len(historia))
        return [historia[-1]]

    ejecutar_backtesting([float(i) for i in range(60)], predictor)
    assert longitudes == sorted(longitudes)
    assert longitudes[-1] == 59
    assert len(set(longitudes)) == len(longitudes)


def test_metricas_admiten_reales_cero_y_sesgo():
    metricas = calcular_metricas([0, 10, 0], [2, 8, 0])
    assert metricas["mae"] == pytest.approx(4 / 3, abs=1e-4)
    assert metricas["wape"] == pytest.approx(0.4)
    assert metricas["sesgo"] == 0


def test_estacional_solo_se_habilita_con_dos_anios():
    corta = _serie([10 + i % 4 for i in range(60)])
    larga = _serie([10 + i % 4 for i in range(110)])
    assert "SEASONAL_NAIVE_52" not in {x["metodo"] for x in pronosticar_serie(corta, fecha_corte=_corte(corta), horizonte=4)["comparacion_metodos"]}
    assert "SEASONAL_NAIVE_52" in {x["metodo"] for x in pronosticar_serie(larga, fecha_corte=_corte(larga), horizonte=4)["comparacion_metodos"]}


def _db_citas(conteos):
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE cita (id INTEGER PRIMARY KEY, fecha_registro TIMESTAMP NOT NULL)")
        cita_id = 1
        inicio = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
        for semana, cantidad in enumerate(conteos):
            for _ in range(cantidad):
                conn.exec_driver_sql(
                    "INSERT INTO cita (id, fecha_registro) VALUES (?, ?)",
                    (cita_id, inicio + timedelta(weeks=semana)),
                )
                cita_id += 1
    return engine


def _cliente_con_db(engine, permiso=True):
    db = Session(engine)
    usuario = SimpleNamespace(id=1)

    def get_db():
        yield db

    def denegar():
        raise HTTPException(status_code=403, detail="Sin permiso")

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[database_session.get_db] = get_db
    app.dependency_overrides[dependencies.get_db] = get_db
    app.dependency_overrides[dependencia_administrador] = lambda: usuario
    app.dependency_overrides[permiso_pronostico] = (lambda: usuario) if permiso else denegar
    return TestClient(app), db, anteriores


def test_endpoint_contrato_sin_pii_y_horizonte_limitado():
    engine = _db_citas([5] * 60)
    client, db, anteriores = _cliente_con_db(engine)
    try:
        respuesta = client.get("/ia-predictiva/demanda/semanal", params={
            "horizonte": 4, "fecha_corte": "2026-03-02T00:00:00Z",
        })
        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["objetivo"] == "CITAS_CREADAS"
        assert len(cuerpo["pronosticos"]) == 4
        texto = respuesta.text.lower()
        assert all(campo not in texto for campo in ["paciente_id", "correo", "motivo", "anamnesis"])
        assert client.get("/ia-predictiva/demanda/semanal?horizonte=13").status_code == 422
    finally:
        client.close(); db.close(); engine.dispose()
        app.dependency_overrides.clear(); app.dependency_overrides.update(anteriores)


def test_endpoint_deniega_usuario_sin_permiso():
    engine = _db_citas([5] * 60)
    client, db, anteriores = _cliente_con_db(engine, permiso=False)
    try:
        assert client.get("/ia-predictiva/demanda/semanal").status_code == 403
    finally:
        client.close(); db.close(); engine.dispose()
        app.dependency_overrides.clear(); app.dependency_overrides.update(anteriores)


def test_aislamiento_tenant_por_sesion_resuelta():
    engine_a, engine_b = _db_citas([3] * 60), _db_citas([9] * 60)
    resultados = []
    for engine in (engine_a, engine_b):
        client, db, anteriores = _cliente_con_db(engine)
        try:
            respuesta = client.get("/ia-predictiva/demanda/semanal", params={
                "horizonte": 4, "fecha_corte": "2026-03-02T00:00:00Z",
            })
            resultados.append(respuesta.json()["pronosticos"][0]["valor_pronosticado"])
        finally:
            client.close(); db.close()
            app.dependency_overrides.clear(); app.dependency_overrides.update(anteriores)
    engine_a.dispose(); engine_b.dispose()
    assert resultados == [3.0, 9.0]
