"""Regresiones CORS especificas del registro administrativo CU31."""

from fastapi.testclient import TestClient

from app.main import app


def test_preflight_registro_pago_admite_cabeceras_reales_de_angular():
    with TestClient(app) as client:
        respuesta = client.options(
            "/pagos/administrativos",
            headers={
                "Origin": "http://localhost:4200",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "authorization,content-type,idempotency-key"
                ),
            },
        )

    assert respuesta.status_code == 200
    assert respuesta.text == "OK"
    assert respuesta.headers["access-control-allow-origin"] == (
        "http://localhost:4200"
    )
    assert "POST" in respuesta.headers["access-control-allow-methods"]
    cabeceras = {
        item.strip().lower()
        for item in respuesta.headers["access-control-allow-headers"].split(",")
    }
    assert {"authorization", "content-type", "idempotency-key"} <= cabeceras


def test_post_registro_pago_supera_cors_y_alcanza_autenticacion():
    with TestClient(app) as client:
        respuesta = client.post(
            "/pagos/administrativos",
            headers={
                "Origin": "http://localhost:4200",
                "Idempotency-Key": "cors-no-payment",
            },
            json={
                "paciente_id": 8,
                "servicio_realizado_ids": [3],
                "metodo_pago": "EFECTIVO",
            },
        )

    # Sin JWT debe detenerse en autenticacion, no en CORS ni en el servicio de
    # pagos. Por tanto, esta prueba nunca persiste un pago.
    assert respuesta.status_code == 401
    assert respuesta.json() == {"detail": "Not authenticated"}
    assert respuesta.headers["access-control-allow-origin"] == (
        "http://localhost:4200"
    )
