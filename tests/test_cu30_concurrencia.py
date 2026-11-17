"""Concurrent CU30 requests must never share a SQLite connection or tenant."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.cu30_entorno_aislado import (
    CORREO_ADMIN,
    PASSWORD_PRUEBA,
    api_aislada_para_pruebas,
)


@pytest.fixture
def flujo_concurrente():
    api = api_aislada_para_pruebas()
    with TestClient(api) as client:
        yield client, api.state.cu30_entorno


@pytest.mark.parametrize("indice", range(8))
def test_conexiones_simultaneas_independientes_comparten_solo_su_base(flujo_concurrente, indice):
    _, entorno = flujo_concurrente
    engine = entorno.control if indice == 0 else entorno.tenants[entorno.empresas[indice - 1]]
    with engine.connect() as primera, engine.connect() as segunda:
        assert primera.connection.driver_connection is not segunda.connection.driver_connection
        if indice == 0:
            sql = "SELECT COUNT(*) FROM saas_control.suscripcion"
            assert primera.exec_driver_sql(sql).scalar_one() == 7
            primera.rollback()
            assert segunda.exec_driver_sql(sql).scalar_one() == 7
        else:
            codigo = entorno.empresas[indice - 1]
            sql = "SELECT nombre FROM public.producto WHERE id=1"
            assert primera.exec_driver_sql(sql).scalar_one() == "Gotas " + codigo
            primera.rollback()
            assert segunda.exec_driver_sql(sql).scalar_one() == "Gotas " + codigo


def test_consultas_http_concurrentes_aislan_los_siete_tenants_con_ids_iguales(flujo_concurrente):
    client, entorno = flujo_concurrente
    headers = {}
    for codigo in entorno.empresas:
        login = client.post("/seguridad/tenant/login", json={
            "empresa_codigo": codigo, "correo": CORREO_ADMIN, "password": PASSWORD_PRUEBA,
        })
        assert login.status_code == 200
        headers[codigo] = {"Authorization": "Bearer " + login.json()["access_token"]}

    rutas = (
        "/seguridad/tenant/empresas", "/seguridad/menu",
        "/inventario-proveedores/inventario", "/inventario-proveedores/alertas",
        "/inventario-proveedores/productos/1/lotes",
    )

    def consultar(tarea):
        codigo, ruta = tarea
        response = client.get(ruta, headers=headers[codigo])
        assert response.status_code == 200, (codigo, ruta, response.text)
        datos = response.json()
        if ruta.endswith("/empresas"):
            assert {e["codigo"] for e in datos} == set(entorno.empresas)
        elif ruta.endswith("/menu"):
            assert datos[0]["funciones"][0]["nombre"] == "Consultar inventario y alertas"
        elif ruta.endswith("/inventario"):
            assert datos["total"] == 7
            assert all(codigo in p["nombre"] for p in datos["items"])
            assert next(p for p in datos["items"] if p["id"] == 1)["stock_actual"] == 10
        elif ruta.endswith("/alertas"):
            assert datos["total"] == 8
            assert all(codigo in a["producto_nombre"] for a in datos["items"])
        else:
            assert datos["producto_id"] == 1
            assert datos["lotes"]
            assert all(l["codigo"].startswith(codigo) for l in datos["lotes"])

    tareas = [(codigo, ruta) for _ in range(3) for codigo in entorno.empresas for ruta in rutas]
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(consultar, tareas))


def test_bases_del_entorno_se_eliminan_al_cerrar_su_ciclo_de_vida():
    api = api_aislada_para_pruebas()
    with TestClient(api):
        entorno = api.state.cu30_entorno
        archivos = []
        for engine in (entorno.control, *entorno.tenants.values()):
            with engine.connect() as db:
                archivos.extend(Path(fila[2]) for fila in db.exec_driver_sql("PRAGMA database_list") if fila[2])
        assert len(archivos) == 16
        assert all(archivo.is_file() for archivo in archivos)
    assert all(not archivo.exists() for archivo in archivos)
