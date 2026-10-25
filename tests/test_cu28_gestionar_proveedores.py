"""CU28 - Gestionar proveedores: CRUD tenant-aware, RBAC y auditoria."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core import dependencies
from app.core.security import crear_access_token, crear_tenant_access_token
from app.database import session
from app.main import app
from app.modules.gestion_inventario_proveedores.models.proveedor import Proveedor
from app.modules.gestion_inventario_proveedores.services import proveedor as service
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
from tests.test_clinical_request_routing import routed_client  # noqa: F401


URL = "/proveedores/"
SQL_CU28 = Path(__file__).resolve().parents[1] / "db" / "cu28_gestionar_proveedores.sql"
DDL_PROVEEDORES = """
    CREATE TABLE proveedores (
        id INTEGER PRIMARY KEY,
        razon_social VARCHAR(150) NOT NULL,
        nit VARCHAR(30) UNIQUE,
        telefono VARCHAR(20),
        correo VARCHAR(100),
        direccion VARCHAR(255),
        contacto VARCHAR(100),
        estado BOOLEAN
    )
"""
DATOS = {
    "razon_social": "Optica Andina SRL",
    "nit": "1020304050",
    "telefono": "70000000",
    "correo": "ventas@example.com",
    "direccion": "Av. Busch 123",
    "contacto": "Carla Rojas",
}


@pytest.fixture
def db_proveedores(db_historial):
    db_historial.execute(text(DDL_PROVEEDORES))
    db_historial.execute(text("""
        INSERT INTO proveedores
            (id, razon_social, nit, telefono, correo, direccion, contacto, estado)
        VALUES
            (1, 'Lentes del Sur', '111', NULL, NULL, NULL, NULL, 1),
            (2, 'Farmacia Vision', '222', NULL, NULL, NULL, NULL, 1),
            (3, 'Proveedor Antiguo', '333', NULL, NULL, NULL, NULL, 0)
    """))
    # Recepcionista: solo LECTURA. Oftalmologo: sin la funcion asignada.
    db_historial.execute(text(
        "INSERT INTO rol VALUES (2, 'Recepcionista', NULL, 1, 0, '2026-01-01'),"
        " (3, 'Oftalmologo', NULL, 1, 0, '2026-01-01')",
    ))
    db_historial.execute(text(
        "INSERT INTO usuario VALUES"
        " (8, 'recepcion@example.test', 'x', 1, '2026-01-01', 2),"
        " (9, 'medico@example.test', 'x', 1, '2026-01-01', 3)",
    ))
    db_historial.execute(text(
        "INSERT INTO funcion VALUES (40, 'Gestionar proveedores', 1)",
    ))
    db_historial.execute(text(
        "INSERT INTO rol_funcion VALUES (40, 1, 40, 3), (41, 2, 40, 1)",
    ))
    db_historial.commit()
    return db_historial


@pytest.fixture
def cliente(db_proveedores):
    def db_local():
        yield db_proveedores

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = db_local
    app.dependency_overrides[dependencies.get_db] = db_local
    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {crear_access_token(7, 1)}"
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)


def autenticar(client, usuario_id, rol_id):
    client.headers["Authorization"] = f"Bearer {crear_access_token(usuario_id, rol_id)}"


def auditorias(db, accion):
    return db.scalars(select(Bitacora).where(Bitacora.accion == accion)).all()


def test_listar_devuelve_activos_ordenados_y_filtra(cliente):
    activos = cliente.get(URL)
    assert activos.status_code == 200, activos.text
    assert [p["razon_social"] for p in activos.json()] == [
        "Farmacia Vision", "Lentes del Sur",
    ]
    inactivos = cliente.get(URL, params={"estado": "false"})
    assert [p["id"] for p in inactivos.json()] == [3]
    por_nit = cliente.get(URL, params={"busqueda": "22"})
    assert [p["id"] for p in por_nit.json()] == [2]
    comodin = cliente.get(URL, params={"busqueda": "%"})
    assert comodin.json() == []


def test_crear_consultar_y_auditar(cliente, db_proveedores):
    respuesta = cliente.post(URL, json={**DATOS, "razon_social": "  Optica Andina SRL  "})
    assert respuesta.status_code == 201, respuesta.text
    creado = respuesta.json()
    assert creado["razon_social"] == "Optica Andina SRL"
    assert creado["estado"] is True

    consulta = cliente.get(f"{URL}{creado['id']}")
    assert consulta.status_code == 200
    assert consulta.json() == creado

    [registro] = auditorias(db_proveedores, "CREAR_PROVEEDOR")
    assert registro.usuario_id == 7
    assert registro.entidad_afectada == "proveedores"
    assert registro.id_registro_afectado == creado["id"]


def test_actualizacion_parcial_y_limpieza_de_opcionales(cliente, db_proveedores):
    respuesta = cliente.put(f"{URL}1", json={"telefono": "71234567", "nit": "  "})
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["telefono"] == "71234567"
    assert respuesta.json()["nit"] is None
    assert respuesta.json()["razon_social"] == "Lentes del Sur"
    assert len(auditorias(db_proveedores, "ACTUALIZAR_PROVEEDOR")) == 1


def test_nit_duplicado_responde_409_en_alta_y_edicion(cliente, db_proveedores):
    assert cliente.post(URL, json={**DATOS, "nit": "111"}).status_code == 409
    assert cliente.put(f"{URL}2", json={"nit": "111"}).status_code == 409
    # Conservar el propio NIT no es un duplicado.
    assert cliente.put(f"{URL}1", json={"nit": "111"}).status_code == 200
    assert db_proveedores.scalar(select(func.count(Proveedor.id))) == 3


def test_nit_vacio_no_choca_con_indice_unico(cliente):
    primero = cliente.post(URL, json={"razon_social": "Sin NIT A", "nit": ""})
    segundo = cliente.post(URL, json={"razon_social": "Sin NIT B", "nit": " "})
    assert primero.status_code == 201, primero.text
    assert segundo.status_code == 201, segundo.text
    assert primero.json()["nit"] is None and segundo.json()["nit"] is None


def test_baja_logica_y_reactivacion(cliente, db_proveedores):
    assert cliente.delete(f"{URL}1").status_code == 204
    proveedor = db_proveedores.get(Proveedor, 1)
    db_proveedores.refresh(proveedor)
    assert proveedor.estado is False
    assert 1 not in [p["id"] for p in cliente.get(URL).json()]
    assert cliente.delete(f"{URL}1").status_code == 409
    assert len(auditorias(db_proveedores, "DESACTIVAR_PROVEEDOR")) == 1

    reactivado = cliente.put(f"{URL}1", json={"estado": True})
    assert reactivado.status_code == 200
    assert reactivado.json()["estado"] is True


def test_404_para_proveedor_inexistente(cliente):
    assert cliente.get(f"{URL}999").status_code == 404
    assert cliente.put(f"{URL}999", json={"telefono": "1"}).status_code == 404
    assert cliente.delete(f"{URL}999").status_code == 404


@pytest.mark.parametrize("datos", [
    {},
    {"razon_social": "   "},
    {"razon_social": "x" * 151},
    {**DATOS, "correo": "no-es-correo"},
    {**DATOS, "nit": "1" * 31},
    {**DATOS, "empresa_id": 2},
])
def test_crear_rechaza_datos_invalidos(cliente, datos):
    assert cliente.post(URL, json=datos).status_code == 422


@pytest.mark.parametrize("datos", [{}, {"razon_social": None}, {"estado": None}])
def test_put_rechaza_vacio_o_null_no_permitido(cliente, datos):
    assert cliente.put(f"{URL}1", json=datos).status_code == 422


def test_requiere_token_y_permiso_rbac(cliente):
    cliente.headers.pop("Authorization")
    assert cliente.get(URL).status_code == 401
    assert cliente.post(URL, json=DATOS).status_code == 401
    assert cliente.delete(f"{URL}1").status_code == 401

    # LECTURA permite consultar, pero no escribir.
    autenticar(cliente, 8, 2)
    assert cliente.get(URL).status_code == 200
    assert cliente.get(f"{URL}1").status_code == 200
    assert cliente.post(URL, json=DATOS).status_code == 403
    assert cliente.put(f"{URL}1", json={"telefono": "1"}).status_code == 403
    assert cliente.delete(f"{URL}1").status_code == 403

    # Sin la funcion asignada no hay acceso alguno.
    autenticar(cliente, 9, 3)
    assert cliente.get(URL).status_code == 403


def test_fallo_de_bitacora_revierte_alta(cliente, db_proveedores, monkeypatch):
    def fallar(*args, **kwargs):
        raise SQLAlchemyError("Fallo simulado")

    monkeypatch.setattr(service, "registrar_bitacora", fallar)
    with pytest.raises(SQLAlchemyError):
        cliente.post(URL, json=DATOS)
    assert db_proveedores.scalar(select(func.count(Proveedor.id))) == 3
    assert db_proveedores.scalar(select(func.count(Bitacora.id))) == 0


def test_proveedores_aislados_entre_tenants(routed_client):
    client, engines, _, _ = routed_client
    for codigo, nombre in (("ALPHA", "Proveedor Alpha"), ("BETA", "Proveedor Beta")):
        with engines[codigo].begin() as conexion:
            conexion.exec_driver_sql(DDL_PROVEEDORES)
            conexion.exec_driver_sql(
                "INSERT INTO proveedores (id, razon_social, nit, estado) VALUES (1, ?, '999', 1)",
                (nombre,),
            )
            conexion.exec_driver_sql("INSERT INTO funcion VALUES (40, 'Gestionar proveedores', 1)")
            conexion.exec_driver_sql("INSERT INTO rol_funcion VALUES (40, 1, 40, 1)")

    alpha = {"Authorization": f"Bearer {crear_tenant_access_token(7, 1, 1, 1, 'ALPHA')}"}
    beta = {"Authorization": f"Bearer {crear_tenant_access_token(7, 1, 2, 2, 'BETA')}"}
    assert client.get(URL, headers=alpha).json()[0]["razon_social"] == "Proveedor Alpha"
    assert client.get(URL, headers=beta).json()[0]["razon_social"] == "Proveedor Beta"

    # El mismo NIT puede existir en otra clinica: la unicidad es por tenant.
    creado = client.post(URL, headers=alpha, json={**DATOS, "nit": "777"})
    assert creado.status_code == 201, creado.text
    assert client.post(URL, headers=beta, json={**DATOS, "nit": "777"}).status_code == 201
    assert client.post(URL, headers=alpha, json={**DATOS, "nit": "999"}).status_code == 409
    with engines["ALPHA"].connect() as conexion:
        assert conexion.scalar(text("SELECT count(*) FROM proveedores")) == 2
    with engines["BETA"].connect() as conexion:
        assert conexion.scalar(text("SELECT count(*) FROM proveedores")) == 2
    with engines["LEGACY"].connect() as conexion:
        assert conexion.scalar(text(
            "SELECT count(*) FROM sqlite_master WHERE name = 'proveedores'",
        )) == 0


def test_openapi_publica_crud_con_baja_logica():
    rutas = app.openapi()["paths"]
    assert set(rutas[URL]) == {"get", "post"}
    assert set(rutas[f"{URL}{{proveedor_id}}"]) == {"get", "put", "delete"}


def test_sql_rbac_es_un_script_atomico_e_idempotente():
    sql = SQL_CU28.read_text(encoding="utf-8")
    controles = re.findall(r"(?im)^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", sql)
    assert controles == ["BEGIN", "COMMIT"]
    assert "ON CONFLICT (modulo_id, nombre) DO NOTHING" in sql
    assert "ON CONFLICT (rol_id, funcion_id) DO NOTHING" in sql
    assert "'Gestionar proveedores'" in sql
    # Sin DDL destructivo ni ids de catalogo hardcodeados.
    assert not re.search(r"(?i)\b(DROP|TRUNCATE|DELETE\s+FROM|ALTER\s+TABLE\s+\S+\s+(ADD|DROP))\b", sql)
    assert not re.search(r"(?i)VALUES\s*\(\s*\d+\s*,", sql)
