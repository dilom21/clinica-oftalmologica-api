from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core import dependencies
from app.main import app
from app.modules.ia_predictiva.api.router import (
    obtener_administrador_actual as dependencia_administrador,
    permiso_inasistencia,
)
from app.modules.ia_predictiva.services.inasistencia import (
    LabelEvent, LabelQualityConfig, LogisticRegressionStdlib, StandardScaler,
    build_features, chronological_split, evaluate_classifier,
    generate_synthetic_dataset, train_and_evaluate_synthetic,
    validate_label_quality,
)


NOW = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)


def _event(index, state, **changes):
    values = dict(appointment_key=str(index), scheduled_at=NOW + timedelta(days=index),
                  state=state, transition_at=NOW + timedelta(days=index, hours=3),
                  source="AUDITED_TRANSITION", previous_state="PROGRAMADA")
    values.update(changes)
    return LabelEvent(**values)


def test_puerta_acepta_etiquetas_sinteticas_trazables():
    events = [_event(i, "NO_ASISTIO" if i % 2 else "ATENDIDA") for i in range(40)]
    result = validate_label_quality(events)
    assert result["status"] == "LABELS_VALIDATED"
    assert result["training_enabled"] is True
    assert result["positives"] == result["negatives"] == 20


@pytest.mark.parametrize("changes", [
    {"source": None}, {"transition_at": None}, {"previous_state": None},
    {"transition_at": NOW}, {"rescheduled": True}, {"state": "CANCELADA"},
    {"state": "PENDIENTE"},
])
def test_puerta_rechaza_estado_actual_cancelada_reprogramada_pendiente_y_cierre_invalido(changes):
    state = changes.pop("state", "NO_ASISTIO")
    assert validate_label_quality([_event(0, state, **changes)])["training_enabled"] is False


def test_puerta_detecta_duplicados_contradictorios_y_bloquea_datos_reales():
    events = [_event(i, "NO_ASISTIO" if i % 2 else "ATENDIDA") for i in range(40)]
    events.append(_event(1, "ATENDIDA"))
    result = validate_label_quality(events, config=LabelQualityConfig(maximum_ambiguous_ratio=.5), real_data=True)
    assert result["status"] == "LABELS_AMBIGUOUS"
    assert any("contradictorias" in reason for reason in result["reasons"])
    assert result["training_enabled"] is False


def test_features_solo_operativas_previas_sin_pii_ni_texto_clinico():
    row = {"registered_at": NOW, "scheduled_at": NOW + timedelta(days=3), "channel": "WEB"}
    assert len(build_features(row, prediction_at=NOW)) == 6
    for forbidden in ("paciente_id", "correo", "motivo", "anamnesis", "estado_final", "fecha_cierre"):
        with pytest.raises(ValueError, match="prohibidos"):
            build_features({**row, forbidden: "secreto"}, prediction_at=NOW)


def test_features_rechazan_fuga_temporal():
    with pytest.raises(ValueError, match="temporal"):
        build_features({"registered_at": NOW, "scheduled_at": NOW - timedelta(hours=1), "channel": "WEB"}, prediction_at=NOW)


def test_split_es_cronologico_y_sin_solapamiento():
    rows = list(reversed(generate_synthetic_dataset(100)))
    train, validation, test = chronological_split(rows)
    assert max(r["scheduled_at"] for r in train) < min(r["scheduled_at"] for r in validation)
    assert max(r["scheduled_at"] for r in validation) < min(r["scheduled_at"] for r in test)
    assert (len(train), len(validation), len(test)) == (60, 20, 20)


def test_metricas_desbalance_calibracion_y_auc_no_calculable():
    metrics = evaluate_classifier([0, 0, 0, 1], [.1, .2, .8, .7])
    assert metrics["confusion_matrix"] == {"tp": 1, "tn": 2, "fp": 1, "fn": 0}
    assert metrics["precision"] == .5 and metrics["recall"] == 1
    assert metrics["pr_auc"] is not None and metrics["roc_auc"] is not None
    assert metrics["brier_score"] >= 0 and metrics["calibration"]
    single = evaluate_classifier([0, 0], [.1, .2])
    assert single["roc_auc"] is None and single["pr_auc"] is None


def test_logistica_exige_ambas_clases_y_scaler_ajusta_solo_train():
    with pytest.raises(ValueError, match="ambas clases"):
        LogisticRegressionStdlib().fit([[0.], [1.]], [0, 0])
    scaler = StandardScaler().fit([[0.], [2.]])
    assert scaler.means == [1.]
    assert scaler.transform([[100.]])[0][0] == 99.


def test_pipeline_sintetico_es_reproducible_compara_baselines_y_no_habilita_real():
    first = train_and_evaluate_synthetic(size=500, seed=17)
    second = train_and_evaluate_synthetic(size=500, seed=17)
    assert first == second
    assert first["dataset"] == "SYNTHETIC_ONLY"
    assert set(first["baselines_test"]) == {"historical_rate", "majority_class"}
    assert first["model_ready_for_real_data"] is False
    assert first["comparison"]["beats_historical_rate_brier"] is False
    assert first["synthetic_status"] == "MODEL_NOT_READY"


def _client(*, permit=True, tenant_marker=None):
    previous = app.dependency_overrides.copy()
    resolved = []
    def get_db():
        resolved.append(tenant_marker)
        yield SimpleNamespace(tenant=tenant_marker)
    def deny(): raise HTTPException(status_code=403, detail="Sin permiso")
    app.dependency_overrides[dependencies.get_db] = get_db
    app.dependency_overrides[dependencia_administrador] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[permiso_inasistencia] = (lambda: SimpleNamespace(id=1)) if permit else deny
    return TestClient(app), previous, resolved


def test_endpoint_protegido_contrato_bloqueado_sin_pii():
    client, previous, resolved = _client(tenant_marker="tenant-a")
    try:
        response = client.get("/ia-predictiva/inasistencia/estado")
        assert response.status_code == 200 and resolved == ["tenant-a"]
        body = response.json()
        assert body["estado"] == "TRAINING_DISABLED"
        assert body["calidad"] == "BLOQUEADO_POR_DATOS"
        assert body["q09_entrenamiento"] == "PROHIBIDO"
        assert body["modelo_habilitado"] is body["prediccion_habilitada"] is False
        assert not any(value in response.text.lower() for value in ("juan", "@example", "+591", "dolor ocular"))
    finally:
        client.close(); app.dependency_overrides.clear(); app.dependency_overrides.update(previous)


def test_endpoint_deniega_sin_permiso_y_no_acepta_selector_tenant():
    client, previous, _ = _client(permit=False)
    try:
        assert client.get("/ia-predictiva/inasistencia/estado").status_code == 403
    finally:
        client.close(); app.dependency_overrides.clear(); app.dependency_overrides.update(previous)
    operation = app.openapi()["paths"]["/ia-predictiva/inasistencia/estado"]["get"]
    assert all(parameter["name"] not in {"tenant_id", "empresa_id", "dsn", "database"} for parameter in operation.get("parameters", []))


def test_aislamiento_resuelve_una_sesion_por_tenant_sin_combinar_datos():
    for marker in ("tenant-a", "tenant-b"):
        client, previous, resolved = _client(tenant_marker=marker)
        try:
            assert client.get("/ia-predictiva/inasistencia/estado").status_code == 200
            assert resolved == [marker]
        finally:
            client.close(); app.dependency_overrides.clear(); app.dependency_overrides.update(previous)
