import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_reportes.schemas.progressive import FiltrosProgresivos, Granularidad
from app.modules.gestion_reportes.services import progressive


@pytest.fixture
def report_client(db_historial):
    db_historial.execute(text("INSERT INTO funcion VALUES (30, 1, 'Generar reportes', 1)"))
    db_historial.execute(text("INSERT INTO rol_funcion VALUES (4, 1, 30, 1)"))
    db_historial.commit()

    def local_db():
        yield db_historial

    old = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = local_db
    app.dependency_overrides[dependencies.get_db] = local_db
    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {crear_access_token(7, 1)}"
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)


def _prepare(db):
    db.execute(text("""CREATE TABLE IF NOT EXISTS consulta_clinica (
        id INTEGER PRIMARY KEY, historial_clinico_id INTEGER NOT NULL,
        cita_id INTEGER, oftalmologo_id INTEGER NOT NULL,
        fecha_consulta TIMESTAMP NOT NULL, motivo_consulta TEXT,
        anamnesis TEXT, observaciones TEXT, estado BOOLEAN NOT NULL
    )"""))
    for index in range(10, 16):
        state = "ATENDIDA" if index < 15 else "NO_ASISTIO"
        db.execute(text("""INSERT INTO cita
            (id,paciente_id,oftalmologo_id,fecha,hora_inicio,hora_fin,estado,canal,
             fecha_registro,fecha_actualizacion)
            VALUES (:id,1,1,:scheduled,'09:00','09:30',:state,'WEB',:created,:updated)"""), {
            "id": index, "scheduled": f"2026-01-{index:02d}", "state": state,
            "created": f"2026-01-{index - 1:02d} 23:30:00",
            "updated": f"2026-01-{index:02d} 10:00:00" if index == 10 else f"2026-01-{index - 1:02d} 23:30:00",
        })
    for index in range(1, 6):
        db.execute(text("INSERT INTO consulta_clinica VALUES (:id,1,NULL,1,:date,NULL,NULL,NULL,1)"),
                   {"id": index, "date": f"2026-01-{index + 9:02d} 15:00:00"})
    db.commit()


def test_kpis_distinguish_created_scheduled_attended_and_privacy(db_historial):
    _prepare(db_historial)
    filters = FiltrosProgresivos(fecha_desde="2026-01-10", fecha_hasta="2026-01-15")
    result = progressive.kpis(db_historial, filters)
    values = {item["indicador"]: item for item in result["indicadores"]}
    assert values["citas_programadas"]["valor"] == 6
    assert values["citas_atendidas"]["valor"] == 5
    assert values["consultas_clinicas"]["valor"] == 5
    assert values["inasistencias_descriptivas"]["valor"] is None
    assert values["inasistencias_descriptivas"]["estado_dato"] == "SUPPRESSED"
    assert values["reprogramaciones_proxy"]["calificador"] == "PROXY"


@pytest.mark.parametrize("granularity,expected", [
    (Granularidad.DIA, 6), (Granularidad.SEMANA, 2), (Granularidad.MES, 1),
])
def test_series_fill_real_zero_and_support_granularities(db_historial, granularity, expected):
    _prepare(db_historial)
    filters = FiltrosProgresivos(fecha_desde="2026-01-10", fecha_hasta="2026-01-15", granularidad=granularity)
    result = progressive.serie(db_historial, "citas_programadas", filters)
    assert len(result["puntos"]) == expected
    if granularity == Granularidad.DIA:
        assert all(point["estado_dato"] == "SUPPRESSED" for point in result["puntos"])


def test_comparison_does_not_compute_variation_from_suppressed_values(db_historial):
    _prepare(db_historial)
    filters = FiltrosProgresivos(fecha_desde="2026-01-10", fecha_hasta="2026-01-15")
    result = progressive.comparar(db_historial, "inasistencias_descriptivas", filters)
    assert result["actual"]["estado_dato"] == "SUPPRESSED"
    assert result["variacion_absoluta"] is None
    assert result["variacion_porcentual"] is None


def test_filters_reject_invalid_ranges_state_and_extra_tenant_fields():
    with pytest.raises(Exception):
        FiltrosProgresivos(fecha_desde="2026-02-01", fecha_hasta="2026-01-01")
    with pytest.raises(Exception):
        FiltrosProgresivos(fecha_desde="2024-01-01", fecha_hasta="2026-01-01")
    with pytest.raises(Exception):
        FiltrosProgresivos(fecha_desde="2026-01-01", fecha_hasta="2026-01-02", estado="INVENTADO")
    with pytest.raises(Exception):
        FiltrosProgresivos(fecha_desde="2026-01-01", fecha_hasta="2026-01-02", tenant_id=9)


def test_catalog_openapi_and_csv_export_are_aggregated(report_client, db_historial):
    _prepare(db_historial)
    catalog = report_client.get("/reportes/progresivos/catalogo")
    assert catalog.status_code == 200
    items = {item["key"]: item for item in catalog.json()["indicadores"]}
    assert items["capacidad_estimada"]["calificador"] == "ESTIMADO"
    assert items["integridad_agenda"]["rango_maximo_dias"] == 92
    assert "BLOQUEADO_POR_DATOS" in items["inasistencias_descriptivas"]["calificador"]

    kpis = report_client.get("/reportes/progresivos/kpis", params={
        "fecha_desde": "2026-01-10", "fecha_hasta": "2026-01-15", "granularidad": "dia",
    })
    assert kpis.status_code == 200, kpis.text
    assert kpis.json()["zona_horaria"]

    response = report_client.post("/reportes/progresivos/exportar/csv", json={
        "fecha_desde": "2026-01-10", "fecha_hasta": "2026-01-15",
        "granularidad": "mes", "indicador": "citas_programadas",
    })
    assert response.status_code == 200, response.text
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0] == ["Período inicio", "Período fin", "Valor", "Estado del dato"]
    forbidden = ("paciente", "documento", "telefono", "correo", "motivo", "anamnesis", "diagnostico")
    assert not any(word in response.text.lower() for word in forbidden)
    paths = report_client.app.openapi()["paths"]
    assert "/reportes/progresivos/kpis" in paths
    assert "/reportes/progresivos/series/{indicador}" in paths


def test_patient_role_and_tenant_are_resolved_by_existing_dependencies(report_client, db_historial):
    db_historial.execute(text("UPDATE rol SET nombre='PACIENTE' WHERE id=1"))
    db_historial.commit()
    response = report_client.get("/reportes/progresivos/catalogo")
    assert response.status_code == 403
    parameters = report_client.app.openapi()["paths"]["/reportes/progresivos/kpis"]["get"]["parameters"]
    assert "tenant_id" not in {item["name"] for item in parameters}
