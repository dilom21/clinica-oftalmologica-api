"""Real tenant login, RBAC menu and CU30 HTTP flow over disposable databases."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tests.cu30_entorno_aislado import (
    CORREO_ADMIN, CORREO_ESCRITURA, CORREO_LECTURA, CORREO_ROL_INACTIVO,
    CORREO_SIN_PERMISO, CORREO_USUARIO_INACTIVO, PASSWORD_PRUEBA,
    api_aislada_para_pruebas,
)


RUTAS_CU30 = (
    "/inventario-proveedores/inventario",
    "/inventario-proveedores/alertas",
    "/inventario-proveedores/productos/1/lotes",
)
NOMBRE_FUNCION = "Consultar inventario y alertas"


@pytest.fixture
def flujo():
    api = api_aislada_para_pruebas()
    with TestClient(api) as client:
        yield client, api.state.cu30_entorno


def _login(flujo, empresa=None, correo=CORREO_ADMIN, password=PASSWORD_PRUEBA):
    client, entorno = flujo
    return client.post("/seguridad/tenant/login", json={
        "empresa_codigo": empresa or entorno.empresas[0], "correo": correo, "password": password,
    })


def _autorizacion(flujo, empresa=None, correo=CORREO_ADMIN):
    response = _login(flujo, empresa, correo)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    return {"Authorization": "Bearer " + body["access_token"]}


def _funciones(menu):
    return [funcion for modulo in menu for funcion in modulo["funciones"]]


def test_selector_publico_siete_empresas_no_expone_bases_o_credenciales(flujo):
    client, entorno = flujo
    response = client.get("/seguridad/tenant/empresas")
    assert response.status_code == 200
    empresas = response.json()
    assert {e["codigo"] for e in empresas} == set(entorno.empresas)
    assert len(empresas) == 7
    assert all(set(e) == {"codigo", "nombre"} for e in empresas)
    assert "database_name" not in response.text and "password" not in response.text


@pytest.mark.parametrize("numero", range(7))
def test_login_menu_y_consultas_reales_en_cada_empresa(flujo, numero):
    client, entorno = flujo
    empresa = entorno.empresas[numero]
    headers = _autorizacion(flujo, empresa)
    menu = client.get("/seguridad/menu", headers=headers)
    assert menu.status_code == 200, menu.text
    funciones = _funciones(menu.json())
    assert len(funciones) == 1
    assert funciones[0]["nombre"] == NOMBRE_FUNCION
    assert funciones[0]["accion_nombre"] == "AMBAS"
    entorno.sentencias.clear()

    inventario = client.get(RUTAS_CU30[0], headers=headers)
    alertas = client.get(RUTAS_CU30[1], headers=headers)
    lotes = client.get(RUTAS_CU30[2], headers=headers)
    assert inventario.status_code == alertas.status_code == lotes.status_code == 200
    datos = inventario.json()
    assert datos["total"] == 7
    assert all(empresa in producto["nombre"] for producto in datos["items"])
    assert next(p for p in datos["items"] if p["id"] == 3)["stock_actual"] == 13 + numero
    assert next(p for p in datos["items"] if p["id"] == 1)["stock_actual"] == 10
    assert datos["resumen"] == {
        "productos": 7, "stock_bajo": 1, "agotados": 3,
        "lotes_proximos": 2, "lotes_vencidos": 2,
    }
    assert all(empresa in aviso["producto_nombre"] for aviso in alertas.json()["items"])
    assert all(lote["codigo"].startswith(empresa) for lote in lotes.json()["lotes"])
    assert {lote["estado_vencimiento"] for lote in lotes.json()["lotes"]} == {
        "VIGENTE", "PROXIMO", "VENCIDO", "SIN_FECHA",
    }
    assert all(codigo == empresa and sql.lstrip().upper().startswith("SELECT")
               for codigo, sql in entorno.sentencias)


@pytest.mark.parametrize("correo,accion,acceso", [
    (CORREO_ADMIN, "AMBAS", 200),
    (CORREO_LECTURA, "LECTURA", 200),
    (CORREO_ESCRITURA, "ESCRITURA", 403),
    (CORREO_SIN_PERMISO, None, 403),
])
def test_menu_y_acceso_usan_asignaciones_reales_del_usuario(flujo, correo, accion, acceso):
    client, _ = flujo
    headers = _autorizacion(flujo, correo=correo)
    menu = client.get("/seguridad/menu", headers=headers)
    assert menu.status_code == 200, menu.text
    funciones = _funciones(menu.json())
    if accion:
        assert [(f["nombre"], f["accion_nombre"]) for f in funciones] == [(NOMBRE_FUNCION, accion)]
    else:
        assert funciones == []
        assert NOMBRE_FUNCION not in menu.text
    for ruta in RUTAS_CU30:
        response = client.get(ruta, headers=headers)
        assert response.status_code == acceso, response.text
        if acceso == 403:
            assert "items" not in response.json()


@pytest.mark.parametrize("correo,password,empresa,status", [
    (CORREO_ADMIN, "incorrecta", None, 401),
    ("desconocido@cu30.test", PASSWORD_PRUEBA, None, 401),
    (CORREO_ADMIN, PASSWORD_PRUEBA, "EMPRESA-INEXISTENTE", 401),
    (CORREO_ROL_INACTIVO, PASSWORD_PRUEBA, None, 403),
    (CORREO_USUARIO_INACTIVO, PASSWORD_PRUEBA, None, 403),
])
def test_login_real_rechaza_credenciales_usuario_y_rol_invalidos(flujo, correo, password, empresa, status):
    response = _login(flujo, empresa, correo, password)
    assert response.status_code == status, response.text
    assert "access_token" not in response.json()


def test_filtros_alertas_lotes_y_paginacion_tras_login(flujo):
    client, _ = flujo
    headers = _autorizacion(flujo, correo=CORREO_LECTURA)
    listado = client.get(RUTAS_CU30[0], headers=headers, params={
        "q": "gc-1", "categoria": "medicamentos", "stock": "BAJO", "vencimiento": "PROXIMO",
    })
    assert listado.status_code == 200, listado.text
    assert listado.json()["total"] == 1
    assert {l["id"] for l in listado.json()["items"][0]["lotes"]} == {1, 2}
    assert listado.json()["items"][0]["stock_actual"] == 10
    assert listado.json()["resumen"]["lotes_proximos"] == 2
    vencidas = client.get(RUTAS_CU30[1], headers=headers, params={"vencimiento": "VENCIDO"})
    assert vencidas.status_code == 200
    assert sum(a["tipo"] == "VENCIDO" for a in vencidas.json()["items"]) == 2
    assert all(a["existencia"] > 0 for a in vencidas.json()["items"] if a["lote_id"] is not None)
    detalle = client.get(RUTAS_CU30[2], headers=headers, params={"dias_vencimiento": 31})
    assert next(l for l in detalle.json()["lotes"] if l["id"] == 3)["estado_vencimiento"] == "PROXIMO"
    primera = client.get(RUTAS_CU30[0], headers=headers, params={"page_size": 2}).json()
    segunda = client.get(RUTAS_CU30[0], headers=headers, params={"page_size": 2, "page": 2}).json()
    assert primera["total_pages"] == segunda["total_pages"] == 4
    assert {p["id"] for p in primera["items"]}.isdisjoint({p["id"] for p in segunda["items"]})
    assert primera["resumen"] == segunda["resumen"]
    vacia = client.get(RUTAS_CU30[0], headers=headers, params={"q": "sin-coincidencia"}).json()
    assert vacia["items"] == [] and vacia["total"] == 0
    invalida = client.get(RUTAS_CU30[0], headers=headers, params={"dias_vencimiento": 366})
    assert invalida.status_code == 422


def test_sesiones_autenticadas_alternadas_no_comparten_ids_datos_o_permisos(flujo):
    client, entorno = flujo
    autorizaciones = [_autorizacion(flujo, empresa) for empresa in entorno.empresas]
    for indice in (6, 0, 3, 1, 5, 2, 4, 0):
        headers = autorizaciones[indice]
        response = client.get(RUTAS_CU30[0], headers=headers)
        assert response.status_code == 200
        assert all(entorno.empresas[indice] in p["nombre"] for p in response.json()["items"])
        propio = client.get(f"/inventario-proveedores/productos/{101 + indice}/lotes", headers=headers)
        ajeno = client.get(f"/inventario-proveedores/productos/{101 + ((indice + 1) % 7)}/lotes", headers=headers)
        assert propio.status_code == 200 and ajeno.status_code == 404
    sin_permiso = _autorizacion(flujo, entorno.empresas[6], CORREO_SIN_PERMISO)
    assert client.get(RUTAS_CU30[0], headers=sin_permiso).status_code == 403
    assert client.get(RUTAS_CU30[0], headers=autorizaciones[0]).status_code == 200


@pytest.mark.parametrize("sql", [
    "UPDATE rol SET estado=0 WHERE id=1",
    "UPDATE modulo SET estado=0 WHERE id=4",
    "UPDATE funcion SET estado=0 WHERE id=30",
    "DELETE FROM rol_funcion WHERE rol_id=1",
    "UPDATE accion SET estado=0 WHERE id=3",
])
def test_revocacion_rbac_inmediata_con_jwt_ya_emitido(flujo, sql):
    client, entorno = flujo
    headers = _autorizacion(flujo)
    assert client.get(RUTAS_CU30[0], headers=headers).status_code == 200
    with entorno.tenants[entorno.empresas[0]].begin() as db:
        db.exec_driver_sql(sql)
    for ruta in RUTAS_CU30:
        response = client.get(ruta, headers=headers)
        assert response.status_code == 403, response.text
        assert "items" not in response.json()


def test_error_de_fuente_no_se_convierta_en_vacio_ni_afecta_otras_empresas(flujo):
    client, entorno = flujo
    primera = _autorizacion(flujo)
    segunda = _autorizacion(flujo, entorno.empresas[1])
    with entorno.tenants[entorno.empresas[0]].begin() as db:
        db.exec_driver_sql("DROP TABLE public.inventario")
    for ruta in RUTAS_CU30:
        response = client.get(ruta, headers=primera)
        assert response.status_code == 503
        assert "items" not in response.json()
        assert "SELECT " not in response.text and "no such table" not in response.text
    assert client.get(RUTAS_CU30[0], headers=segunda).status_code == 200


def test_harness_bloquea_conexiones_no_sqlite(flujo):
    externo = create_engine("postgresql+psycopg://cu30:unused@invalid.example/cu30")
    try:
        with pytest.raises(RuntimeError, match="bloquea bases de datos externas"):
            externo.connect()
    finally:
        externo.dispose()


def test_marcador_identifica_entorno_aislado_sin_exponer_datos(flujo):
    response = flujo[0].get("/__cu30_test/status")
    assert response.status_code == 200
    assert response.json() == {"entorno": "cu30-aislado", "empresas": 7}


def test_harness_restaura_factories_y_no_modifica_global_app():
    from app.database import session
    from app.main import app as global_app

    anterior = session.SessionLocal
    overrides = global_app.dependency_overrides.copy()
    api = api_aislada_para_pruebas()
    with TestClient(api) as client:
        assert session.SessionLocal is not anterior
        assert client.get("/seguridad/tenant/empresas").status_code == 200
    assert session.SessionLocal is anterior
    assert global_app.dependency_overrides == overrides


def test_plan_y_habilitacion_siete_empresas_habilita_menu_y_consultas_solo_admin(flujo):
    from app.modules.gestion_inventario_proveedores.services import permisos_inventario as permisos

    client, entorno = flujo
    tokens = {}
    for empresa, engine in entorno.tenants.items():
        with engine.begin() as db:
            # Reproduce the verified real prerequisite: the CU30 function is absent.
            db.exec_driver_sql("DELETE FROM rol_funcion WHERE funcion_id=30")
            db.exec_driver_sql("DELETE FROM funcion WHERE id=30")
        tokens[empresa] = _autorizacion(flujo, empresa)
        assert _funciones(client.get("/seguridad/menu", headers=tokens[empresa]).json()) == []
        assert client.get(RUTAS_CU30[0], headers=tokens[empresa]).status_code == 403

    entorno.sentencias.clear()
    factory = lambda: Session(entorno.control)
    plan = permisos.preparar_plan(factory, entorno.registry)
    assert plan.to_dict()["totales"] == {
        "empresas": 7, "funciones_nuevas": 7,
        "permisos_lectura_nuevos": 7, "auditorias_nuevas": 7,
    }
    assert all(sql.lstrip().upper().startswith("SELECT") for _, sql in entorno.sentencias)
    resultado = permisos.aplicar_plan(factory, entorno.registry, plan_hash=plan.huella)
    assert resultado["estado"] == "COMPLETADO", resultado
    assert set(resultado["aplicadas"]) == set(entorno.empresas)

    for empresa, engine in entorno.tenants.items():
        menu = client.get("/seguridad/menu", headers=tokens[empresa])
        assert menu.status_code == 200
        assert [(f["nombre"], f["accion_nombre"]) for f in _funciones(menu.json())] == [(NOMBRE_FUNCION, "LECTURA")]
        for ruta in RUTAS_CU30:
            assert client.get(ruta, headers=tokens[empresa]).status_code == 200
        lectura_sin_asignacion = _autorizacion(flujo, empresa, CORREO_LECTURA)
        assert client.get(RUTAS_CU30[0], headers=lectura_sin_asignacion).status_code == 403
        with Session(engine) as db:
            assert db.scalar(text("SELECT count(*) FROM funcion WHERE nombre=:nombre"), {"nombre": NOMBRE_FUNCION}) == 1
            assert db.scalar(text("SELECT count(*) FROM rol_funcion")) == 1
            assert db.scalar(text("SELECT rol_id FROM rol_funcion")) == 1
            assert db.scalar(text("SELECT count(*) FROM bitacora WHERE accion='HABILITAR_CU30'")) == 1

    segundo = permisos.preparar_plan(factory, entorno.registry)
    assert segundo.to_dict()["totales"]["funciones_nuevas"] == 0
    assert segundo.to_dict()["totales"]["permisos_lectura_nuevos"] == 0
    repetido = permisos.aplicar_plan(factory, entorno.registry, plan_hash=segundo.huella)
    assert repetido["estado"] == "COMPLETADO" and repetido["aplicadas"] == []
    assert set(repetido["sin_cambios"]) == set(entorno.empresas)
