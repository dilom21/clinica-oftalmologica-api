"""CU22: HTTP, permisos, relaciones reales y transacciones en SQLite."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_historial_clinico.models.models import ServicioRealizado
from app.modules.gestion_historial_clinico.services import servicios_realizados as service
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
from tests.test_cu15_consulta_clinica import db_cu15  # fixture clínica ya existente


URL = "/historial-clinico/servicios-realizados"
DATOS = {
    "servicio_id": 1, "paciente_id": 1, "consulta_clinica_id": 500, "precio_aplicado": 150.50,
    "fecha_realizacion": "2026-09-04T09:00:00-04:00", "observaciones": "  Fondo de ojo  ",
}


@pytest.fixture
def db_cu22(db_cu15):
    db = db_cu15
    for sql in (
        """CREATE TABLE servicio_oftalmologico (
            id INTEGER PRIMARY KEY, nombre VARCHAR(150) NOT NULL,
            descripcion TEXT, precio NUMERIC(10,2), duracion INTEGER, estado BOOLEAN
        )""",
        """CREATE TABLE servicio_realizado (
            id INTEGER PRIMARY KEY,
            servicio_id INTEGER NOT NULL REFERENCES servicio_oftalmologico(id) ON DELETE RESTRICT,
            paciente_id INTEGER NOT NULL REFERENCES paciente(id) ON DELETE RESTRICT,
            consulta_clinica_id INTEGER REFERENCES consulta_clinica(id) ON DELETE SET NULL,
            oftalmologo_id INTEGER NOT NULL REFERENCES oftalmologo(id) ON DELETE RESTRICT,
            fecha_realizacion TIMESTAMP, observaciones TEXT, estado BOOLEAN,
            precio_aplicado NUMERIC(10,2) CHECK (precio_aplicado >= 0)
        )""",
        """INSERT INTO servicio_oftalmologico VALUES
            (1, 'Fondo de ojo', 'Examen', 150.50, 20, 1),
            (2, 'Servicio inactivo', NULL, 80, 10, 0),
            (3, 'Sin precio', NULL, NULL, NULL, 1),
            (4, 'Servicio sin costo', NULL, 0, 10, 1),
            (5, 'Servicio alternativo', NULL, 12.50, 10, 1)""",
        """INSERT INTO consulta_clinica (id,historial_clinico_id,oftalmologo_id,fecha_consulta,estado)
            VALUES (501,20,1,'2026-09-03 08:00:00',1)""",
        "INSERT INTO funcion VALUES (30, 'Registrar servicios realizados', 1)",
        """INSERT INTO rol_funcion VALUES
            (30, 1, 30, 3), (31, 2, 30, 1), (32, 3, 30, 2)""",
    ):
        db.execute(text(sql))
    db.commit()
    yield db


@pytest.fixture
def cliente_cu22(db_cu22):
    def local_db():
        yield db_cu22

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = local_db
    app.dependency_overrides[dependencies.get_db] = local_db
    try:
        with TestClient(app) as client:
            autenticar(client, 7, 1)
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)


def autenticar(client, usuario_id, rol_id):
    client.headers["Authorization"] = f"Bearer {crear_access_token(usuario_id, rol_id)}"


def cantidad(db, modelo):
    return db.scalar(select(func.count()).select_from(modelo))


def registrar(client, **cambios):
    return client.post(URL, json=DATOS | cambios)


def test_registro_con_consulta_y_bitacora(cliente_cu22, db_cu22):
    respuesta = registrar(cliente_cu22)
    assert respuesta.status_code == 201, respuesta.text
    datos = respuesta.json()
    assert datos["oftalmologo_id"] == 1  # derivado del JWT
    assert datos["observaciones"] == "Fondo de ojo"
    assert datos["servicio"]["precio_base"] == 150.50
    assert datos["precio_aplicado"] == 150.50
    assert datos["paciente"] == {"id": 1, "nombres": "Ana", "apellidos": "Pérez"}
    assert cantidad(db_cu22, ServicioRealizado) == 1
    bitacora = db_cu22.scalar(select(Bitacora))
    assert bitacora.accion == "REGISTRAR_SERVICIO_REALIZADO"
    assert bitacora.usuario_id == 7
    assert bitacora.id_registro_afectado == datos["id"]


def test_catalogo_sin_precio_no_permite_importe_manual(cliente_cu22, db_cu22):
    respuesta = registrar(cliente_cu22, servicio_id=3, precio_aplicado=0)
    assert respuesta.status_code == 409, respuesta.text
    assert "catálogo CU21" in respuesta.json()["detail"]
    assert cantidad(db_cu22, ServicioRealizado) == 0
    assert cantidad(db_cu22, Bitacora) == 0


def test_catalogo_precio_cero_se_registra_sin_importe_del_cliente(cliente_cu22):
    respuesta = cliente_cu22.post(URL, json={"servicio_id": 4, "paciente_id": 1})
    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["consulta_clinica_id"] is None
    assert respuesta.json()["fecha_realizacion"] is not None
    assert respuesta.json()["servicio"]["precio_base"] == 0
    assert respuesta.json()["precio_aplicado"] == 0


@pytest.mark.parametrize("cambios,codigo", [
    ({"servicio_id": 999}, 404), ({"servicio_id": 2}, 409),
    ({"paciente_id": 999}, 404), ({"paciente_id": 5}, 409),
    ({"paciente_id": 2}, 409), ({"consulta_clinica_id": 999}, 404),
    ({"fecha_realizacion": "2026-09-02T09:00:00Z"}, 422),
    ({"fecha_realizacion": "2026-09-04T09:00:00"}, 422),
    ({"fecha_realizacion": "2099-01-01T00:00:00Z"}, 422),
    ({"paciente_id": 0}, 422), ({"servicio_id": 2**63}, 422),
    ({"oftalmologo_id": 2}, 422), ({"estado": False}, 422),
])
def test_rechaza_datos_sin_insertar(cliente_cu22, db_cu22, cambios, codigo):
    assert registrar(cliente_cu22, **cambios).status_code == codigo
    assert cantidad(db_cu22, ServicioRealizado) == 0
    assert cantidad(db_cu22, Bitacora) == 0


@pytest.mark.parametrize("sql,codigo", [
    ("UPDATE consulta_clinica SET estado=0 WHERE id=500", 409),
    ("UPDATE historial_clinico SET estado=0 WHERE id=10", 409),
    ("UPDATE consulta_clinica SET oftalmologo_id=2 WHERE id=500", 403),
    ("UPDATE oftalmologo SET estado=0 WHERE id=1", 403),
])
def test_valida_consulta_historial_y_profesional(cliente_cu22, db_cu22, sql, codigo):
    db_cu22.execute(text(sql))
    db_cu22.commit()
    assert registrar(cliente_cu22).status_code == codigo
    assert cantidad(db_cu22, ServicioRealizado) == 0


def test_autenticacion_y_permiso(cliente_cu22, db_cu22):
    cliente_cu22.headers.pop("Authorization")
    assert registrar(cliente_cu22).status_code == 401
    autenticar(cliente_cu22, 13, 5)
    assert registrar(cliente_cu22).status_code == 403
    autenticar(cliente_cu22, 8, 2)  # permiso de lectura solamente
    assert registrar(cliente_cu22).status_code == 403
    assert cliente_cu22.get(URL).status_code == 200
    autenticar(cliente_cu22, 9, 3)  # escritura, pero no profesional clínico
    assert registrar(cliente_cu22).status_code == 403
    assert cliente_cu22.get(URL).status_code == 403
    autenticar(cliente_cu22, 11, 1)  # oftalmólogo sin perfil
    assert registrar(cliente_cu22).status_code == 403


@pytest.mark.parametrize("accion", ["LECTURA", "ESCRITURA", "AMBAS"])
def test_acciones_del_permiso(cliente_cu22, db_cu22, accion):
    db_cu22.execute(text("""
        UPDATE rol_funcion SET accion_id=(SELECT id FROM accion WHERE nombre=:accion)
        WHERE rol_id=1 AND funcion_id=30
    """), {"accion": accion})
    db_cu22.commit()
    assert registrar(cliente_cu22).status_code == (403 if accion == "LECTURA" else 201)
    assert cliente_cu22.get(URL).status_code == (403 if accion == "ESCRITURA" else 200)


def test_actualiza_conservando_fecha_y_anula_sin_borrar(cliente_cu22, db_cu22):
    original = registrar(cliente_cu22).json()
    registro_id = original["id"]
    respuesta = cliente_cu22.put(f"{URL}/{registro_id}", json={
        "servicio_id": 5, "paciente_id": 1, "consulta_clinica_id": 500,
        "precio_aplicado": 12.50, "observaciones": "  corregido  ",
    })
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["observaciones"] == "corregido"
    assert respuesta.json()["fecha_realizacion"] == original["fecha_realizacion"]
    assert respuesta.json()["consulta_clinica_id"] == 500
    assert respuesta.json()["precio_aplicado"] == 12.50
    assert cliente_cu22.get(f"{URL}/{registro_id}").status_code == 200
    assert cliente_cu22.delete(f"{URL}/{registro_id}").json()["estado"] is False
    assert cliente_cu22.get(URL).json()["total"] == 0
    assert cliente_cu22.get(URL, params={"estado": "false"}).json()["total"] == 1
    assert cliente_cu22.put(f"{URL}/{registro_id}", json=DATOS).status_code == 409
    assert cliente_cu22.delete(f"{URL}/{registro_id}").status_code == 200
    assert cantidad(db_cu22, ServicioRealizado) == 1
    assert cantidad(db_cu22, Bitacora) == 3  # anulación repetida no duplica bitácora


@pytest.mark.parametrize("metodo", ["put", "delete"])
def test_no_modifica_registros_de_otro_medico(cliente_cu22, db_cu22, metodo):
    registro_id = registrar(cliente_cu22).json()["id"]
    autenticar(cliente_cu22, 12, 1)
    respuesta = (cliente_cu22.put(f"{URL}/{registro_id}", json=DATOS)
                 if metodo == "put" else cliente_cu22.delete(f"{URL}/{registro_id}"))
    assert respuesta.status_code == 403
    assert db_cu22.get(ServicioRealizado, registro_id).estado is True
    assert cantidad(db_cu22, Bitacora) == 1


@pytest.mark.parametrize("metodo", ["get", "put", "delete"])
def test_registro_no_encontrado(cliente_cu22, metodo):
    kwargs = {"json": DATOS} if metodo == "put" else {}
    assert getattr(cliente_cu22, metodo)(f"{URL}/999", **kwargs).status_code == 404


def test_listado_filtros_orden_y_paginacion(cliente_cu22):
    for dia in (4, 5, 6):
        assert registrar(cliente_cu22, fecha_realizacion=f"2026-09-0{dia}T12:00:00Z").status_code == 201
    assert registrar(cliente_cu22, paciente_id=2, consulta_clinica_id=501).status_code == 201
    respuesta = cliente_cu22.get(URL, params={
        "paciente_id": 1, "servicio_id": 1, "consulta_clinica_id": 500,
        "oftalmologo_id": 1, "page": 2, "page_size": 1,
    })
    datos = respuesta.json()
    assert respuesta.status_code == 200
    assert (datos["total"], datos["total_pages"], datos["page"]) == (3, 3, 2)
    assert datos["items"][0]["fecha_realizacion"].startswith("2026-09-05")
    assert cliente_cu22.get(URL, params={"desde": "2026-09-05T00:00:00Z", "hasta": "2026-09-06T00:00:00Z"}).json()["total"] == 1
    assert cliente_cu22.get(URL, params={"paciente_id": 999}).json()["total"] == 0


@pytest.mark.parametrize("params", [
    {"page_size": 101}, {"page": 0}, {"paciente_id": -1},
    {"desde": "2026-09-06T00:00:00Z", "hasta": "2026-09-04T00:00:00Z"},
    {"desde": "2026-09-06T00:00:00"},
])
def test_filtros_invalidos(cliente_cu22, params):
    assert cliente_cu22.get(URL, params=params).status_code == 422


@pytest.mark.parametrize("operacion", ["crear", "actualizar", "anular"])
def test_fallo_bitacora_revierte_toda_la_operacion(cliente_cu22, db_cu22, monkeypatch, operacion):
    registro_id = None
    if operacion != "crear":
        registro_id = registrar(cliente_cu22).json()["id"]
    def fallar(*args, **kwargs):
        raise SQLAlchemyError("Fallo de bitácora simulado")
    monkeypatch.setattr(service, "registrar_bitacora", fallar)
    with pytest.raises(SQLAlchemyError):
        if operacion == "crear":
            registrar(cliente_cu22)
        elif operacion == "actualizar":
            cliente_cu22.put(f"{URL}/{registro_id}", json=DATOS | {"observaciones": "nuevo"})
        else:
            cliente_cu22.delete(f"{URL}/{registro_id}")
    assert cantidad(db_cu22, ServicioRealizado) == (0 if operacion == "crear" else 1)
    assert cantidad(db_cu22, Bitacora) == (0 if operacion == "crear" else 1)
    if registro_id:
        registro = db_cu22.get(ServicioRealizado, registro_id)
        assert registro.estado is True
        assert registro.observaciones == "Fondo de ojo"


def test_cu21_conserva_contrato_y_cu22_usa_sus_ids(cliente_cu22):
    respuesta = cliente_cu22.post("/servicios-oftalmologicos/", json={
        "nombre": "Tonometría", "precio_base": 100.25, "duracion_estimada": 10,
    })
    assert respuesta.status_code == 201, respuesta.text
    servicio_id = respuesta.json()["id"]
    assert cliente_cu22.get(f"/servicios-oftalmologicos/{servicio_id}").json()["precio_base"] == 100.25
    actualizado = cliente_cu22.put(f"/servicios-oftalmologicos/{servicio_id}", json={"precio_base": 110.50})
    assert actualizado.status_code == 200
    realizado = registrar(cliente_cu22, servicio_id=servicio_id, precio_aplicado=110.50)
    assert realizado.status_code == 201, realizado.text
    assert realizado.json()["servicio"]["precio_base"] == 110.50

def test_fallo_commit_revierte_registro_y_bitacora(cliente_cu22, db_cu22, monkeypatch):
    def fallar():
        raise SQLAlchemyError("Commit fallido")
    monkeypatch.setattr(db_cu22, "commit", fallar)
    with pytest.raises(SQLAlchemyError):
        registrar(cliente_cu22)
    assert cantidad(db_cu22, ServicioRealizado) == 0
    assert cantidad(db_cu22, Bitacora) == 0


def test_actualizacion_invalida_conserva_registro(cliente_cu22, db_cu22):
    original = registrar(cliente_cu22).json()
    respuesta = cliente_cu22.put(f"{URL}/{original['id']}", json=DATOS | {"paciente_id": 2})
    assert respuesta.status_code == 409
    assert cliente_cu22.get(f"{URL}/{original['id']}").json() == original
    assert cantidad(db_cu22, Bitacora) == 1


def test_fecha_con_zona_se_guarda_en_utc(cliente_cu22, db_cu22):
    respuesta = registrar(cliente_cu22)
    assert respuesta.status_code == 201
    guardado = db_cu22.get(ServicioRealizado, respuesta.json()["id"])
    assert guardado.fecha_realizacion.hour == 13  # 09:00 en Bolivia = 13:00 UTC
    filtrado = cliente_cu22.get(URL, params={
        "desde": "2026-09-04T08:59:59-04:00", "hasta": "2026-09-04T09:00:01-04:00",
    })
    assert filtrado.json()["total"] == 1


def test_catalogo_desactivado_no_oculta_registros_historicos(cliente_cu22, db_cu22):
    registro_id = registrar(cliente_cu22).json()["id"]
    db_cu22.execute(text("UPDATE servicio_oftalmologico SET estado=0 WHERE id=1"))
    db_cu22.commit()
    assert cliente_cu22.get(f"{URL}/{registro_id}").status_code == 200
    assert cliente_cu22.get(URL).json()["total"] == 1
    assert registrar(cliente_cu22).status_code == 409


def test_fk_postgres_se_traduce_a_conflicto_y_revierte(cliente_cu22, db_cu22, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    class ReferenciaEliminada(Exception):
        sqlstate = "23503"
    def fallar(*args):
        raise IntegrityError("INSERT", {}, ReferenciaEliminada("FK"))
    monkeypatch.setattr(service.repo, "guardar_registro", fallar)
    assert registrar(cliente_cu22).status_code == 409
    assert cantidad(db_cu22, ServicioRealizado) == 0
    assert cantidad(db_cu22, Bitacora) == 0

@pytest.mark.parametrize("precio", [-0.01, "texto", True, "NaN", "Infinity", 1.001, 100000000])
def test_precio_aplicado_invalido(cliente_cu22, db_cu22, precio):
    assert registrar(cliente_cu22, precio_aplicado=precio).status_code == 422
    assert cantidad(db_cu22, ServicioRealizado) == 0
    assert cantidad(db_cu22, Bitacora) == 0


@pytest.mark.parametrize("campo", ["servicio_id", "paciente_id"])
def test_campos_nuevos_obligatorios(cliente_cu22, campo):
    datos = DATOS.copy()
    datos.pop(campo)
    assert cliente_cu22.post(URL, json=datos).status_code == 422
    assert registrar(cliente_cu22, **{campo: None}).status_code == 422


def lote(**cambios):
    return {
        "paciente_id": 1, "consulta_clinica_id": 500,
        "fecha_realizacion": DATOS["fecha_realizacion"],
        "servicios": [
            {"servicio_id": 1, "precio_aplicado": 150.50, "observaciones": "  Primera atención  "},
            {"servicio_id": 4, "precio_aplicado": 0, "observaciones": "  "},
        ],
    } | cambios


def test_lote_persiste_cada_precio_y_observacion_y_audita(cliente_cu22, db_cu22):
    respuesta = cliente_cu22.post(f"{URL}/lote", json=lote())
    assert respuesta.status_code == 201, respuesta.text
    items = respuesta.json()
    assert len(items) == 2
    assert [r["precio_aplicado"] for r in items] == [150.50, 0]
    assert [r["observaciones"] for r in items] == ["Primera atención", None]
    assert all(r["consulta_clinica_id"] == 500 and r["oftalmologo_id"] == 1 for r in items)
    assert cantidad(db_cu22, ServicioRealizado) == 2
    assert cantidad(db_cu22, Bitacora) == 2
    historial = cliente_cu22.get(URL, params={"consulta_clinica_id": 500, "paciente_id": 1}).json()
    assert historial["total"] == 2
    assert {r["precio_aplicado"] for r in historial["items"]} == {150.50, 0}


@pytest.mark.parametrize("cambios,codigo", [
    ({"servicios": []}, 422),
    ({"servicios": [{"servicio_id": 1, "precio_aplicado": -1}]}, 422),
    ({"servicios": [{"servicio_id": 1, "precio_aplicado": 150.50}, {"servicio_id": 2, "precio_aplicado": 80}]}, 409),
    ({"servicios": [{"servicio_id": 999, "precio_aplicado": 5}]}, 404),
    ({"consulta_clinica_id": 999}, 404),
    ({"consulta_clinica_id": -1}, 422),
    ({"paciente_id": 2}, 409),
])
def test_lote_invalido_no_deja_registros_parciales(cliente_cu22, db_cu22, cambios, codigo):
    respuesta = cliente_cu22.post(f"{URL}/lote", json=lote(**cambios))
    assert respuesta.status_code == codigo, respuesta.text
    assert cantidad(db_cu22, ServicioRealizado) == 0
    assert cantidad(db_cu22, Bitacora) == 0


def test_lote_requiere_jwt_y_permiso(cliente_cu22):
    cliente_cu22.headers.pop("Authorization")
    assert cliente_cu22.post(f"{URL}/lote", json=lote()).status_code == 401
    autenticar(cliente_cu22, 8, 2)
    assert cliente_cu22.post(f"{URL}/lote", json=lote()).status_code == 403


@pytest.mark.parametrize("fallo", ["bitacora", "commit"])
def test_lote_revierte_todas_las_filas_y_auditorias(cliente_cu22, db_cu22, monkeypatch, fallo):
    if fallo == "commit":
        def fallar():
            raise SQLAlchemyError("Fallo simulado")
        monkeypatch.setattr(db_cu22, "commit", fallar)
    else:
        original = service.registrar_bitacora
        llamadas = 0
        def fallar(*args, **kwargs):
            nonlocal llamadas
            llamadas += 1
            if llamadas == 2:
                raise SQLAlchemyError("Fallo simulado en la segunda bitácora")
            return original(*args, **kwargs)
        monkeypatch.setattr(service, "registrar_bitacora", fallar)
    with pytest.raises(SQLAlchemyError):
        cliente_cu22.post(f"{URL}/lote", json=lote())
    assert cantidad(db_cu22, ServicioRealizado) == 0
    assert cantidad(db_cu22, Bitacora) == 0


def test_precio_historico_no_cambia_al_editar_catalogo(cliente_cu22):
    registrado = registrar(cliente_cu22).json()
    assert cliente_cu22.put("/servicios-oftalmologicos/1", json={"precio_base": 999}).status_code == 200
    historial = cliente_cu22.get(f"{URL}/{registrado['id']}").json()
    assert historial["servicio"]["precio_base"] == 999
    assert historial["precio_aplicado"] == 150.50


def test_historico_sin_precio_no_inventa_importe(cliente_cu22, db_cu22):
    db_cu22.execute(text("""
        INSERT INTO servicio_realizado (servicio_id,paciente_id,oftalmologo_id,estado)
        VALUES (1,1,1,1)
    """))
    db_cu22.commit()
    datos = cliente_cu22.get(URL).json()["items"][0]
    assert datos["precio_aplicado"] is None
    assert datos["consulta_clinica_id"] is None


def test_no_impone_unicidad_de_servicio_en_la_consulta(cliente_cu22):
    lineas = [{"servicio_id": 1, "precio_aplicado": 150.50, "observaciones": "Primera"},
              {"servicio_id": 1, "precio_aplicado": 150.50, "observaciones": "Segunda"}]
    assert cliente_cu22.post(f"{URL}/lote", json=lote(servicios=lineas)).status_code == 201


@pytest.mark.parametrize("consulta", [None, "omitida"])
def test_consulta_opcional_con_precio_automatico(cliente_cu22, consulta):
    datos = {**DATOS}
    if consulta == "omitida":
        datos.pop("consulta_clinica_id")
    else:
        datos["consulta_clinica_id"] = consulta
    datos.pop("precio_aplicado")
    respuesta = cliente_cu22.post(URL, json=datos)
    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["consulta_clinica_id"] is None
    assert respuesta.json()["precio_aplicado"] == 150.50
    assert cliente_cu22.get(URL, params={"paciente_id": 1}).json()["total"] == 1


def test_lote_sin_consulta_usa_precios_del_catalogo(cliente_cu22):
    datos = lote(consulta_clinica_id=None)
    for linea in datos["servicios"]:
        linea.pop("precio_aplicado")
    respuesta = cliente_cu22.post(f"{URL}/lote", json=datos)
    assert respuesta.status_code == 201, respuesta.text
    assert [r["precio_aplicado"] for r in respuesta.json()] == [150.50, 0]
    assert all(r["consulta_clinica_id"] is None for r in respuesta.json())


@pytest.mark.parametrize("operacion", ["crear", "lote", "actualizar"])
@pytest.mark.parametrize("precio", [0, 1, 140.25, 151])
def test_no_permite_modificar_precio_por_api(cliente_cu22, db_cu22, operacion, precio):
    if operacion == "actualizar":
        registro = registrar(cliente_cu22).json()
        respuesta = cliente_cu22.put(f"{URL}/{registro['id']}", json={**DATOS, "precio_aplicado": precio})
    elif operacion == "lote":
        respuesta = cliente_cu22.post(f"{URL}/lote", json=lote(
            servicios=[{"servicio_id": 1, "precio_aplicado": precio}],
        ))
    else:
        respuesta = registrar(cliente_cu22, precio_aplicado=precio)
    assert respuesta.status_code == 409, respuesta.text
    assert "no puede modificarse" in respuesta.json()["detail"]
    assert cantidad(db_cu22, ServicioRealizado) == (1 if operacion == "actualizar" else 0)
    assert cantidad(db_cu22, Bitacora) == (1 if operacion == "actualizar" else 0)


def test_editar_observaciones_conserva_precio_historico_aunque_cambie_catalogo(cliente_cu22):
    registrado = registrar(cliente_cu22).json()
    assert cliente_cu22.put("/servicios-oftalmologicos/1", json={"precio_base": 999}).status_code == 200
    respuesta = cliente_cu22.put(f"{URL}/{registrado['id']}", json={**DATOS, "observaciones": "Corregido"})
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["precio_aplicado"] == 150.50
    assert respuesta.json()["servicio"]["precio_base"] == 999


def test_historico_sin_importe_se_puede_editar_sin_inventar_precio(cliente_cu22, db_cu22):
    db_cu22.execute(text("""
        INSERT INTO servicio_realizado (id,servicio_id,paciente_id,oftalmologo_id,estado)
        VALUES (77,1,1,1,1)
    """))
    db_cu22.commit()
    respuesta = cliente_cu22.put(f"{URL}/77", json={
        "servicio_id": 1, "paciente_id": 1, "observaciones": "Observación corregida",
    })
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["precio_aplicado"] is None
    assert respuesta.json()["consulta_clinica_id"] is None
    assert cantidad(db_cu22, Bitacora) == 1


def test_put_puede_quitar_consulta_sin_modificar_precio(cliente_cu22):
    registrado = registrar(cliente_cu22).json()
    datos = {**DATOS, "consulta_clinica_id": None}
    respuesta = cliente_cu22.put(f"{URL}/{registrado['id']}", json=datos)
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["consulta_clinica_id"] is None
    assert respuesta.json()["precio_aplicado"] == 150.50
