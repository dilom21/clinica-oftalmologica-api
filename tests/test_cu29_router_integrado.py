"""Arranque ASGI/OpenAPI del Backend completo sin conexiones de datos."""

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.main import app


def test_arranque_openapi_y_rutas_conservadas(monkeypatch):
    def prohibir(*args, **kwargs):
        raise AssertionError("El arranque/OpenAPI no debe conectar a ninguna base")

    monkeypatch.setattr(Engine, "connect", prohibir)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        esperados = {
            "/compras/proveedores": {"get"},
            "/compras/productos": {"get"},
            "/compras/productos/{producto_id}/lotes": {"get"},
            "/compras/": {"get", "post"},
            "/compras/{compra_id}": {"get"},
        }
        for path, methods in esperados.items():
            assert set(paths[path]) == methods
        assert "/proveedores/" in paths
        for prefix in ("/inventario-proveedores", "/notificaciones-chatbot", "/notificaciones", "/reportes", "/ia-predictiva"):
            assert any(p == prefix or p.startswith(prefix + "/") for p in paths)
        identifiers = [operation["operationId"] for path in paths.values() for operation in path.values() if isinstance(operation, dict) and "operationId" in operation]
        assert len(identifiers) == len(set(identifiers)), "Routers registrados dos veces"
