from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable


FEATURES_VERSION = "inasistencia-features-v1"
LABEL_RULE_VERSION = "inasistencia-label-v1"
MODEL_VERSION = "inasistencia-logistic-stdlib-v1"
PREDICTION_MOMENT = "INMEDIATAMENTE_DESPUES_DE_REGISTRAR_LA_CITA"
FEATURE_NAMES = ("antelacion_horas", "dia_semana", "hora_inicio", "mes", "canal_movil", "canal_web")
FORBIDDEN_FIELDS = {
    "id", "paciente_id", "tenant_id", "empresa_id", "oftalmologo_id", "nombre",
    "nombres", "apellidos", "correo", "telefono", "ci", "motivo", "observaciones",
    "diagnostico", "anamnesis", "estado", "estado_final", "fecha_actualizacion", "fecha_cierre",
}


@dataclass(frozen=True)
class LabelEvent:
    appointment_key: str
    scheduled_at: datetime
    state: str
    transition_at: datetime | None
    source: str | None
    previous_state: str | None = None
    rescheduled: bool = False


@dataclass(frozen=True)
class LabelQualityConfig:
    minimum_per_class: int = 20
    maximum_ambiguous_ratio: float = .10
    closure_grace_hours: int = 2


def validate_label_quality(events: Iterable[LabelEvent], *, config=LabelQualityConfig(), real_data=False) -> dict:
    """Puerta fail-closed: Q09 o el estado actual por sí solos nunca son evidencia."""
    rows, seen, valid, reasons = list(events), {}, [], []
    ambiguous = 0
    for event in rows:
        state = event.state.strip().upper()
        invalid = (
            state not in {"ATENDIDA", "NO_ASISTIO"} or event.rescheduled
            or event.transition_at is None or event.source != "AUDITED_TRANSITION"
            or event.previous_state is None
            or event.transition_at < event.scheduled_at + timedelta(hours=config.closure_grace_hours)
        )
        prior = seen.get(event.appointment_key)
        if prior is not None:
            invalid = True
            if prior.state.strip().upper() != state:
                reasons.append("Existen transiciones contradictorias para una cita.")
        seen[event.appointment_key] = event
        if invalid:
            ambiguous += 1
        else:
            valid.append(1 if state == "NO_ASISTIO" else 0)
    positives, negatives = sum(valid), len(valid) - sum(valid)
    ratio = ambiguous / len(rows) if rows else 1.0
    if not rows: reasons.append("No hay etiquetas candidatas.")
    if ambiguous: reasons.append("Hay etiquetas sin transición auditada, tardía y no contradictoria.")
    if positives < config.minimum_per_class or negatives < config.minimum_per_class:
        reasons.append("No se alcanza el mínimo técnico provisional por clase.")
    if ratio > config.maximum_ambiguous_ratio:
        reasons.append("La proporción ambigua supera el umbral técnico provisional.")
    if real_data:
        reasons.append("El entrenamiento real no está autorizado y Q09 sigue BLOQUEADO_POR_DATOS.")
    ready = not reasons
    return {
        "status": "LABELS_VALIDATED" if ready else (
            "LABELS_INSUFFICIENT" if not rows or min(positives, negatives) < config.minimum_per_class
            else "LABELS_AMBIGUOUS"),
        "training_enabled": ready and not real_data, "total": len(rows), "valid": len(valid),
        "ambiguous": ambiguous, "ambiguous_ratio": round(ratio, 4), "positives": positives,
        "negatives": negatives, "rule_version": LABEL_RULE_VERSION,
        "thresholds_provisional": True, "reasons": list(dict.fromkeys(reasons)),
    }


def build_features(row: dict, *, prediction_at: datetime) -> list[float]:
    forbidden = FORBIDDEN_FIELDS.intersection(row)
    if forbidden:
        raise ValueError(f"Campos prohibidos o posteriores: {', '.join(sorted(forbidden))}")
    if not {"registered_at", "scheduled_at", "channel"}.issubset(row):
        raise ValueError("Faltan variables operativas requeridas")
    registered, scheduled = row["registered_at"], row["scheduled_at"]
    if registered > prediction_at or scheduled <= registered:
        raise ValueError("Inconsistencia temporal o fuga posterior al instante de predicción")
    channel = str(row["channel"] or "").upper()
    return [(scheduled - registered).total_seconds() / 3600, float(scheduled.isoweekday()),
            float(scheduled.hour), float(scheduled.month), float(channel == "MOVIL"), float(channel == "WEB")]


def chronological_split(rows: list[dict], train_ratio=.60, validation_ratio=.20):
    if len(rows) < 5: raise ValueError("Se requieren al menos cinco observaciones")
    ordered = sorted(rows, key=lambda row: row["scheduled_at"])
    n_train, n_validation = max(1, int(len(rows) * train_ratio)), max(1, int(len(rows) * validation_ratio))
    if n_train + n_validation >= len(rows): raise ValueError("No queda un conjunto de prueba temporal")
    return ordered[:n_train], ordered[n_train:n_train + n_validation], ordered[n_train + n_validation:]


class StandardScaler:
    def fit(self, x):
        self.means = [sum(col) / len(col) for col in zip(*x)]
        self.scales = [math.sqrt(sum((row[i] - mean) ** 2 for row in x) / len(x)) or 1.0
                       for i, mean in enumerate(self.means)]
        return self

    def transform(self, x):
        return [[(value - self.means[i]) / self.scales[i] for i, value in enumerate(row)] for row in x]


class LogisticRegressionStdlib:
    def __init__(self, *, seed=42, iterations=800, learning_rate=.08):
        self.seed, self.iterations, self.learning_rate = seed, iterations, learning_rate

    def fit(self, x, y):
        if set(y) != {0, 1}: raise ValueError("La regresión logística requiere ambas clases")
        random.Random(self.seed)
        self.weights = [0.0] * (len(x[0]) + 1)
        class_weights = {1: len(y) / (2 * sum(y)), 0: len(y) / (2 * (len(y) - sum(y)))}
        for _ in range(self.iterations):
            gradients = [0.0] * len(self.weights)
            for row, label in zip(x, y):
                error = (self._probability(row) - label) * class_weights[label]
                gradients[0] += error
                for index, value in enumerate(row, 1): gradients[index] += error * value
            for index in range(len(self.weights)):
                self.weights[index] -= self.learning_rate * gradients[index] / len(x)
        return self

    def _probability(self, row):
        score = self.weights[0] + sum(w * value for w, value in zip(self.weights[1:], row))
        return 1 / (1 + math.exp(-max(-35., min(35., score))))

    def predict_proba(self, x): return [self._probability(row) for row in x]


def _roc_auc(labels, scores):
    positives, negatives = sum(labels), len(labels) - sum(labels)
    if not positives or not negatives: return None
    positive_scores = [score for score, label in zip(scores, labels) if label]
    negative_scores = [score for score, label in zip(scores, labels) if not label]
    favorable = sum((p > n) + .5 * (p == n) for p in positive_scores for n in negative_scores)
    return favorable / (positives * negatives)


def _pr_auc(labels, scores):
    if not sum(labels): return None
    tp = fp = 0; previous_recall = area = 0.; ranked = sorted(zip(scores, labels), reverse=True)
    index = 0
    while index < len(ranked):
        score = ranked[index][0]; group = []
        while index < len(ranked) and ranked[index][0] == score:
            group.append(ranked[index][1]); index += 1
        tp += sum(group); fp += len(group) - sum(group)
        recall, precision = tp / sum(labels), tp / (tp + fp)
        area += (recall - previous_recall) * precision; previous_recall = recall
    return area


def evaluate_classifier(labels, probabilities, threshold=.5):
    if not labels or len(labels) != len(probabilities) or not 0 < threshold < 1:
        raise ValueError("Etiquetas, probabilidades o umbral inválidos")
    predictions = [int(value >= threshold) for value in probabilities]
    tp = sum(a == b == 1 for a, b in zip(labels, predictions)); tn = sum(a == b == 0 for a, b in zip(labels, predictions))
    fp = sum(a == 0 and b == 1 for a, b in zip(labels, predictions)); fn = sum(a == 1 and b == 0 for a, b in zip(labels, predictions))
    precision = tp / (tp + fp) if tp + fp else 0.; recall = tp / (tp + fn) if tp + fn else 0.
    calibration = []
    for lower in (0, .2, .4, .6, .8):
        bucket = [(y, p) for y, p in zip(labels, probabilities) if lower <= p < lower + .2 or (lower == .8 and p == 1)]
        if bucket: calibration.append({"lower": lower, "upper": lower + .2, "count": len(bucket),
            "mean_probability": sum(p for _, p in bucket) / len(bucket), "observed_rate": sum(y for y, _ in bucket) / len(bucket)})
    return {"confusion_matrix": {"tp": tp, "tn": tn, "fp": fp, "fn": fn}, "precision": precision,
            "recall": recall, "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.,
            "pr_auc": _pr_auc(labels, probabilities), "roc_auc": _roc_auc(labels, probabilities),
            "brier_score": sum((p - y) ** 2 for y, p in zip(labels, probabilities)) / len(labels),
            "prevalence": sum(labels) / len(labels), "false_positive_rate": fp / (fp + tn) if fp + tn else None,
            "false_negative_rate": fn / (fn + tp) if fn + tp else None, "threshold": threshold, "calibration": calibration}


def generate_synthetic_dataset(size=600, seed=42):
    rng, start, rows = random.Random(seed), datetime(2024, 1, 1, tzinfo=timezone.utc), []
    for index in range(size):
        scheduled = start + timedelta(days=index, hours=8 + index % 9); lead = rng.randint(12, 24 * 45)
        channel = "MOVIL" if rng.random() < .45 else "WEB"
        risk = -2.7 + .45 * (scheduled.isoweekday() >= 6) + .55 * (lead > 24 * 21) + .35 * (channel == "MOVIL")
        rows.append({"registered_at": scheduled - timedelta(hours=lead), "scheduled_at": scheduled,
                     "channel": channel, "label": int(rng.random() < 1 / (1 + math.exp(-risk)))})
    return rows


def train_and_evaluate_synthetic(*, size=600, seed=42, threshold=.5):
    train, validation, test = chronological_split(generate_synthetic_dataset(size, seed))
    to_x = lambda part: [build_features(row, prediction_at=row["registered_at"]) for row in part]
    scaler = StandardScaler().fit(to_x(train)); y_train = [row["label"] for row in train]
    model = LogisticRegressionStdlib(seed=seed).fit(scaler.transform(to_x(train)), y_train)
    metrics = lambda part, probs: evaluate_classifier([row["label"] for row in part], probs, threshold)
    rate, majority = sum(y_train) / len(y_train), float(sum(y_train) / len(y_train) >= .5)
    test_metrics = metrics(test, model.predict_proba(scaler.transform(to_x(test))))
    historical_metrics = metrics(test, [rate] * len(test))
    beats_baseline = test_metrics["brier_score"] < historical_metrics["brier_score"]
    return {"dataset": "SYNTHETIC_ONLY", "seed": seed,
            "sizes": {"train": len(train), "validation": len(validation), "test": len(test)},
            "versions": {"features": FEATURES_VERSION, "labels": LABEL_RULE_VERSION, "model": MODEL_VERSION},
            "validation": metrics(validation, model.predict_proba(scaler.transform(to_x(validation)))),
            "test": test_metrics,
            "baselines_test": {"historical_rate": historical_metrics,
                               "majority_class": metrics(test, [majority] * len(test))},
            "comparison": {"metric": "BRIER_SCORE", "beats_historical_rate_brier": beats_baseline},
            "synthetic_status": "MODEL_READY" if beats_baseline else "MODEL_NOT_READY",
            "model_ready_for_real_data": False}


def real_model_status():
    return {"estado": "TRAINING_DISABLED", "algoritmos_disponibles": ["MAJORITY_CLASS_BASELINE", "HISTORICAL_RATE_BASELINE", "LOGISTIC_REGRESSION_STDLIB"],
            "variables_permitidas": list(FEATURE_NAMES), "variables_excluidas": sorted(FORBIDDEN_FIELDS),
            "razones_bloqueo": ["Cita conserva estado actual, no un historial íntegro de transiciones.",
                "La bitácora no conserva el estado anterior ni una regla verificable de cierre.",
                "NO_ASISTIO puede asignarse sin una validación temporal posterior a la cita.",
                "Q09 es descriptiva y permanece BLOQUEADO_POR_DATOS para entrenamiento.",
                "No existe autorización para entrenamiento con datos reales."],
            "advertencias": ["El resultado sintético solo valida el pipeline técnico.",
                "La estimación nunca cancela, bloquea, prioriza ni penaliza citas; requiere supervisión humana."]}
