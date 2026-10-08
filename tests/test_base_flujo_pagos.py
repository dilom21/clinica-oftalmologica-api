from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_historial_clinico.models.models import (
    ServicioOftalmologico,
    ServicioRealizado,
)
from app.modules.gestion_pagos.models.models import Pago, PagoDetalle
from app.modules.gestion_pagos.repositories import repository as repo
from app.modules.gestion_pagos.schemas.schemas import SeleccionServiciosPago
from app.modules.gestion_pagos.services import service


@pytest.fixture
def db_pagos():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    ddls = [
        """CREATE TABLE rol (
            id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT,
            estado BOOLEAN NOT NULL, protegido BOOLEAN NOT NULL,
            fecha_creacion TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE usuario (
            id INTEGER PRIMARY KEY, correo TEXT NOT NULL, password_hash TEXT NOT NULL,
            estado BOOLEAN NOT NULL, fecha_creacion TIMESTAMP NOT NULL,
            rol_id INTEGER NOT NULL REFERENCES rol(id)
        )""",
        """CREATE TABLE paciente (
            id INTEGER PRIMARY KEY, usuario_id INTEGER UNIQUE REFERENCES usuario(id),
            nombres TEXT NOT NULL, apellidos TEXT NOT NULL, ci TEXT,
            fecha_nacimiento DATE, sexo TEXT, telefono TEXT,
            contacto_emergencia TEXT, fecha_registro TIMESTAMP NOT NULL,
            direccion TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE oftalmologo (
            id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL UNIQUE,
            matricula TEXT NOT NULL UNIQUE, nombres TEXT NOT NULL,
            apellidos TEXT NOT NULL, especialidad TEXT, estado BOOLEAN NOT NULL,
            fecha_registro TIMESTAMP NOT NULL
        )""",
        """CREATE TABLE historial_clinico (
            id INTEGER PRIMARY KEY, paciente_id INTEGER NOT NULL UNIQUE,
            fecha_apertura TIMESTAMP NOT NULL, observaciones_generales TEXT,
            estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE consulta_clinica (
            id INTEGER PRIMARY KEY, historial_clinico_id INTEGER NOT NULL,
            cita_id INTEGER UNIQUE, oftalmologo_id INTEGER NOT NULL,
            fecha_consulta TIMESTAMP NOT NULL, motivo_consulta TEXT,
            anamnesis TEXT, observaciones TEXT, estado BOOLEAN NOT NULL
        )""",
        """CREATE TABLE servicio_oftalmologico (
            id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT,
            precio NUMERIC(10,2), duracion INTEGER, estado BOOLEAN
        )""",
        """CREATE TABLE servicio_realizado (
            id INTEGER PRIMARY KEY, servicio_id INTEGER NOT NULL,
            paciente_id INTEGER NOT NULL, consulta_clinica_id INTEGER,
            oftalmologo_id INTEGER NOT NULL, fecha_realizacion TIMESTAMP,
            observaciones TEXT, estado BOOLEAN, precio_aplicado NUMERIC(10,2)
        )""",
        """CREATE TABLE pago (
            id INTEGER PRIMARY KEY,
            fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            fecha_hora_pago TIMESTAMP, monto NUMERIC(12,2) NOT NULL,
            moneda TEXT NOT NULL, metodo_pago TEXT NOT NULL,
            estado TEXT NOT NULL, pasarela TEXT,
            referencia_transaccion TEXT UNIQUE, observaciones TEXT
        )""",
        """CREATE TABLE pago_detalle (
            id INTEGER PRIMARY KEY, pago_id INTEGER NOT NULL,
            servicio_realizado_id INTEGER NOT NULL,
            monto_aplicado NUMERIC(12,2) NOT NULL,
            UNIQUE(pago_id, servicio_realizado_id)
        )""",
        """CREATE TABLE bitacora (
            id INTEGER PRIMARY KEY, usuario_id INTEGER,
            fecha_hora TIMESTAMP NOT NULL, ip TEXT, accion TEXT NOT NULL,
            entidad_afectada TEXT, id_registro_afectado INTEGER,
            descripcion TEXT
        )""",
    ]
    ahora = "2026-10-01 12:00:00"
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in ddls:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(
            "INSERT INTO rol VALUES (1,'Paciente',NULL,1,0,?),"
            "(2,'Administrador',NULL,1,0,?)",
            (ahora, ahora),
        )
        conn.exec_driver_sql(
            "INSERT INTO usuario VALUES "
            "(11,'p1@test','x',1,?,1),(12,'p2@test','x',1,?,1),"
            "(13,'admin@test','x',1,?,2),(14,'off@test','x',1,?,1),"
            "(99,'none@test','x',1,?,1)",
            (ahora, ahora, ahora, ahora, ahora),
        )
        conn.exec_driver_sql(
            "INSERT INTO paciente VALUES "
            "(1,11,'Ana','Uno',NULL,NULL,NULL,NULL,NULL,?,NULL,1),"
            "(2,12,'Beto','Dos',NULL,NULL,NULL,NULL,NULL,?,NULL,1),"
            "(3,14,'Inactivo','Tres',NULL,NULL,NULL,NULL,NULL,?,NULL,0)",
            (ahora, ahora, ahora),
        )
        conn.exec_driver_sql(
            "INSERT INTO oftalmologo VALUES "
            "(5,99,'MAT-5','Olga','Vista','General',1,?)",
            (ahora,),
        )
        conn.exec_driver_sql(
            "INSERT INTO historial_clinico VALUES "
            "(10,1,?,NULL,1),(20,2,?,NULL,1)",
            (ahora, ahora),
        )
        conn.exec_driver_sql(
            "INSERT INTO consulta_clinica VALUES "
            "(100,10,NULL,5,?,NULL,NULL,NULL,1),"
            "(200,20,NULL,5,?,NULL,NULL,NULL,1)",
            (ahora, ahora),
        )
        conn.exec_driver_sql(
            "INSERT INTO servicio_oftalmologico VALUES "
            "(1,'Tonometria',NULL,50.00,20,1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO servicio_realizado VALUES "
            "(1000,1,1,100,5,?,NULL,1,10.10),"
            "(1001,1,1,100,5,?,NULL,1,20.20),"
            "(1002,1,2,200,5,?,NULL,1,30.30),"
            "(1003,1,1,100,5,?,NULL,0,40.40),"
            "(1004,1,1,100,5,?,NULL,1,NULL),"
            "(1005,1,1,100,5,?,NULL,1,0),"
            "(1006,1,1,NULL,5,?,NULL,1,60.60),"
            "(1007,1,1,200,5,?,NULL,1,70.70)",
            (ahora,) * 8,
        )
        conn.exec_driver_sql(
            "INSERT INTO pago VALUES "
            "(500,?,? ,20.20,'BOB','TARJETA','APROBADO','TEST','ok',NULL),"
            "(501,?,NULL,10.10,'BOB','TARJETA','PENDIENTE','TEST','p',NULL),"
            "(502,?,NULL,10.10,'BOB','TARJETA','RECHAZADO','TEST','r',NULL),"
            "(503,?,NULL,10.10,'BOB','TARJETA','ANULADO','TEST','a',NULL)",
            (ahora, ahora, ahora, ahora, ahora),
        )
        conn.exec_driver_sql(
            "INSERT INTO pago_detalle VALUES "
            "(1,500,1001,20.20),(2,501,1000,10.10),"
            "(3,502,1000,10.10),(4,503,1000,10.10)"
        )
    with Session(engine, autoflush=False) as db:
        yield db
    engine.dispose()


@pytest.fixture
def cliente_pagos(db_pagos):
    def db_local():
        yield db_pagos

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = db_local
    app.dependency_overrides[dependencies.get_db] = db_local
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)


def token(usuario_id: int, rol_id: int = 1):
    return {"Authorization": f"Bearer {crear_access_token(usuario_id, rol_id)}"}


def test_modelos_mapean_tablas_y_relaciones_reales():
    assert Pago.__table__.name == "pago"
    assert PagoDetalle.__table__.name == "pago_detalle"
    assert ServicioOftalmologico.__table__.name == "servicio_oftalmologico"
    assert ServicioRealizado.__table__.name == "servicio_realizado"
    assert Pago.detalles.property.back_populates == "pago"
    assert PagoDetalle.pago.property.back_populates == "detalles"
    assert PagoDetalle.servicio_realizado.property.mapper.class_ is ServicioRealizado
    assert Pago.__table__.c.monto.type.precision == 12
    assert PagoDetalle.__table__.c.monto_aplicado.type.scale == 2


def test_schema_rechaza_seleccion_vacia_y_duplicada():
    with pytest.raises(ValueError):
        SeleccionServiciosPago(servicio_realizado_ids=[])
    with pytest.raises(ValueError):
        SeleccionServiciosPago(servicio_realizado_ids=[1000, 1000])


def test_repositorios_pago_y_cobertura(db_pagos):
    assert repo.obtener_pago_por_id(db_pagos, 500).referencia_transaccion == "ok"
    assert repo.obtener_pago_por_referencia_transaccion(db_pagos, "ok").id == 500
    assert len(repo.listar_detalles_pago(db_pagos, 500)) == 1
    assert [p.id for p in repo.listar_pagos_por_servicio_realizado(db_pagos, 1000)] == [503, 502, 501]
    assert repo.servicio_cubierto_por_pago_aprobado(db_pagos, 1001) is True
    assert repo.servicio_cubierto_por_pago_aprobado(db_pagos, 1000) is False


def test_repositorio_lista_solo_servicios_realmente_pendientes(db_pagos):
    ids = [
        servicio.id
        for servicio in repo.listar_servicios_pendientes_pago(
            db_pagos,
            paciente_id=1,
            consulta_clinica_id=100,
        )
    ]
    assert ids == [1000]


def test_pendiente_rechazado_y_anulado_no_bloquean(db_pagos):
    resultado = service.validar_seleccion_servicios(db_pagos, [1000])
    assert resultado.total == Decimal("10.10")
    assert resultado.consulta_id == 100


def test_pago_aprobado_bloquea_nuevo_intento(db_pagos):
    with pytest.raises(HTTPException) as error:
        service.validar_seleccion_servicios(db_pagos, [1001])
    assert error.value.status_code == 409


@pytest.mark.parametrize("ids", [[], [1000, 1000], [0]])
def test_seleccion_invalida(ids, db_pagos):
    with pytest.raises(HTTPException) as error:
        service.validar_seleccion_servicios(db_pagos, ids)
    assert error.value.status_code == 422


def test_seleccion_id_inexistente(db_pagos):
    with pytest.raises(HTTPException) as error:
        service.validar_seleccion_servicios(db_pagos, [9999])
    assert error.value.status_code == 404


@pytest.mark.parametrize("servicio_id", [1003, 1004, 1005, 1006])
def test_servicio_no_pagable_es_rechazado(servicio_id, db_pagos):
    with pytest.raises(HTTPException) as error:
        service.validar_seleccion_servicios(db_pagos, [servicio_id])
    assert error.value.status_code == 422


def test_seleccion_exige_mismo_paciente_y_consulta(db_pagos):
    with pytest.raises(HTTPException) as error_paciente:
        service.validar_seleccion_servicios(db_pagos, [1000, 1002])
    assert error_paciente.value.status_code == 422

    with pytest.raises(HTTPException) as error_consulta:
        service.validar_seleccion_servicios(db_pagos, [1000, 1007])
    assert error_consulta.value.status_code == 422


def test_consulta_debe_pertenecer_al_paciente(db_pagos):
    with pytest.raises(HTTPException) as error:
        service.validar_seleccion_servicios(db_pagos, [1007])
    assert error.value.status_code == 422


def test_seleccion_respeta_paciente_autenticado(db_pagos):
    with pytest.raises(HTTPException) as error:
        service.validar_seleccion_servicios(
            db_pagos,
            [1000],
            paciente_id_esperado=2,
        )
    assert error.value.status_code == 403


def test_total_usa_decimal_sin_float(db_pagos):
    db_pagos.execute(PagoDetalle.__table__.delete().where(PagoDetalle.pago_id == 500))
    resultado = service.validar_seleccion_servicios(db_pagos, [1000, 1001])
    assert resultado.total == Decimal("30.30")
    assert isinstance(resultado.total, Decimal)


def test_endpoint_lista_solo_consultas_propias(cliente_pagos):
    respuesta = cliente_pagos.get("/pagos/mis-consultas", headers=token(11))
    assert respuesta.status_code == 200
    assert [item["consulta_id"] for item in respuesta.json()] == [100]
    item = respuesta.json()[0]
    assert item["cantidad_servicios"] == 2
    assert item["cantidad_pendientes"] == 1
    assert Decimal(item["total_pendiente"]) == Decimal("10.10")
    assert item["oftalmologo"]["id"] == 5


def test_endpoint_servicios_deriva_estado_pago(cliente_pagos):
    respuesta = cliente_pagos.get(
        "/pagos/mis-consultas/100/servicios",
        headers=token(11),
    )
    assert respuesta.status_code == 200
    estados = {
        item["servicio_realizado_id"]: item["estado_pago"]
        for item in respuesta.json()
    }
    assert estados == {1001: "PAGADO", 1000: "PENDIENTE"}


def test_endpoint_no_expone_consulta_ajena(cliente_pagos):
    respuesta = cliente_pagos.get(
        "/pagos/mis-consultas/200/servicios",
        headers=token(11),
    )
    assert respuesta.status_code == 404


def test_endpoints_requieren_autenticacion_y_rol_paciente(cliente_pagos):
    assert cliente_pagos.get("/pagos/mis-consultas").status_code in {401, 403}
    assert cliente_pagos.get(
        "/pagos/mis-consultas", headers=token(13, 2),
    ).status_code == 403


@pytest.mark.parametrize(
    ("usuario_id", "esperado"),
    [(99, 404), (14, 403)],
)
def test_perfil_ausente_o_inactivo(cliente_pagos, usuario_id, esperado):
    respuesta = cliente_pagos.get(
        "/pagos/mis-consultas",
        headers=token(usuario_id),
    )
    assert respuesta.status_code == esperado


def test_openapi_publica_solo_endpoints_lectura_de_esta_base():
    esquema = app.openapi()
    assert "/pagos/mis-consultas" in esquema["paths"]
    assert "/pagos/mis-consultas/{consulta_id}/servicios" in esquema["paths"]
    assert set(esquema["paths"]["/pagos/mis-consultas"]) == {"get"}
