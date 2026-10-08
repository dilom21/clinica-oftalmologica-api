"""ETAPA 8.1 - Pruebas del historial de pagos y comprobantes PDF.

Cubren los 24 escenarios exigidos usando una base SQLite en memoria aislada.
No se conecta a Supabase, no se llama a Stripe y no se generan cobros.
"""

from datetime import datetime
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_pagos.models.models import Pago, PagoDetalle
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora


DDLS = [
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


def _sembrar(conn) -> None:
    conn.exec_driver_sql(
        "INSERT INTO rol VALUES (1,'Paciente',NULL,1,0,'2026-01-01'),"
        "(2,'Administrador',NULL,1,0,'2026-01-01')"
    )
    conn.exec_driver_sql(
        "INSERT INTO usuario VALUES "
        "(11,'p1@test','x',1,'2026-01-01',1),(12,'p2@test','x',1,'2026-01-01',1),"
        "(13,'admin@test','x',1,'2026-01-01',2),(14,'inact@test','x',1,'2026-01-01',1),"
        "(15,'p4@test','x',1,'2026-01-01',1),(99,'none@test','x',1,'2026-01-01',1)"
    )
    conn.exec_driver_sql(
        "INSERT INTO paciente VALUES "
        "(1,11,'Ana María','Pérez Gutiérrez',NULL,NULL,NULL,NULL,NULL,"
        "'2026-01-01',NULL,1),"
        "(2,12,'Beto','Dos',NULL,NULL,NULL,NULL,NULL,'2026-01-01',NULL,1),"
        "(3,14,'Inactivo','Tres',NULL,NULL,NULL,NULL,NULL,'2026-01-01',NULL,0),"
        "(4,15,'Cero','Pagos',NULL,NULL,NULL,NULL,NULL,'2026-01-01',NULL,1)"
    )
    conn.exec_driver_sql(
        "INSERT INTO oftalmologo VALUES "
        "(5,99,'MAT-5','Olga','Vista','General',1,'2026-01-01')"
    )
    conn.exec_driver_sql(
        "INSERT INTO historial_clinico VALUES "
        "(10,1,'2026-01-01',NULL,1),(20,2,'2026-01-01',NULL,1)"
    )
    conn.exec_driver_sql(
        "INSERT INTO consulta_clinica VALUES "
        "(100,10,NULL,5,'2026-10-01 12:00:00',NULL,NULL,NULL,1),"
        "(101,10,NULL,5,'2026-10-02 12:00:00',NULL,NULL,NULL,1),"
        "(200,20,NULL,5,'2026-10-03 12:00:00',NULL,NULL,NULL,1)"
    )
    conn.exec_driver_sql(
        "INSERT INTO servicio_oftalmologico VALUES "
        "(1,'Consulta General Oftalmológica',NULL,999.00,30,1),"
        "(2,'Medición de Lentes',NULL,999.00,20,1),"
        "(3,'Evaluación Integral de Fondo de Ojo y Medición de Presión "
        "Intraocular con Dilatación Pupilar',NULL,500.00,45,1)"
    )
    conn.exec_driver_sql(
        "INSERT INTO servicio_realizado VALUES "
        "(1000,1,1,100,5,'2026-10-01 12:00:00',NULL,1,150.00),"
        "(1001,2,1,100,5,'2026-10-01 12:00:00',NULL,1,60.00),"
        "(1002,1,1,101,5,'2026-10-02 12:00:00',NULL,1,150.00),"
        "(1003,2,1,101,5,'2026-10-02 12:00:00',NULL,1,60.00),"
        "(1004,1,1,101,5,'2026-10-02 12:00:00',NULL,1,150.00),"
        "(1005,2,1,101,5,'2026-10-02 12:00:00',NULL,1,60.00),"
        "(1006,1,1,100,5,'2026-10-01 12:00:00',NULL,1,150.00),"
        "(1010,3,1,100,5,'2026-10-01 12:00:00',NULL,1,150.00),"
        "(1011,2,1,100,5,'2026-10-01 12:00:00',NULL,1,60.00),"
        "(1012,1,1,100,5,'2026-10-01 12:00:00',NULL,1,30.00),"
        "(2000,1,2,200,5,'2026-10-03 12:00:00',NULL,1,150.00),"
        "(2001,2,2,200,5,'2026-10-03 12:00:00',NULL,1,60.00)"
    )
    conn.exec_driver_sql(
        "INSERT INTO pago VALUES "
        "(500,'2026-10-05 18:30:00','2026-10-05 18:30:00',210.00,'BOB',"
        "'TARJETA','APROBADO','STRIPE','pi_500',NULL),"
        "(501,'2026-10-06 10:00:00',NULL,150.00,'BOB','TARJETA','PENDIENTE',"
        "'STRIPE','pi_501',NULL),"
        "(502,'2026-10-04 09:00:00',NULL,60.00,'BOB','TARJETA','RECHAZADO',"
        "'STRIPE','pi_502',NULL),"
        "(503,'2026-10-03 09:00:00',NULL,150.00,'BOB','TARJETA','ANULADO',"
        "'STRIPE','pi_503',NULL),"
        "(504,'2026-10-02 09:00:00',NULL,60.00,'BOB','TARJETA','REEMBOLSADO',"
        "'STRIPE','pi_504',NULL),"
        "(505,'2026-10-07 08:15:00','2026-10-07 08:15:00',240.00,'BOB',"
        "'TARJETA','APROBADO','STRIPE','pi_505',NULL),"
        "(600,'2026-10-08 09:00:00','2026-10-08 09:00:00',150.00,'BOB',"
        "'TARJETA','APROBADO','STRIPE','pi_600',NULL),"
        "(700,'2026-10-01 09:00:00','2026-10-01 09:00:00',210.00,'BOB',"
        "'TARJETA','APROBADO','STRIPE','pi_700',NULL)"
    )
    conn.exec_driver_sql(
        "INSERT INTO pago_detalle VALUES "
        "(1,500,1000,150.00),(2,500,1001,60.00),(3,501,1002,150.00),"
        "(4,502,1003,60.00),(5,503,1004,150.00),(6,504,1005,60.00),"
        "(7,505,1010,150.00),(8,505,1011,60.00),(9,505,1012,30.00),"
        "(10,600,2000,150.00),(11,700,1006,150.00),(12,700,2001,60.00)"
    )


@pytest.fixture
def db_pagos_historial():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in DDLS:
            conn.exec_driver_sql(ddl)
        _sembrar(conn)
    with Session(engine, autoflush=False) as db:
        yield db
    engine.dispose()


@pytest.fixture
def cliente_historial_pagos(db_pagos_historial):
    def db_local():
        yield db_pagos_historial

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


def _texto_pdf(contenido: bytes) -> str:
    lector = PdfReader(BytesIO(contenido))
    return "\n".join(pagina.extract_text() or "" for pagina in lector.pages)


def _contar(db, modelo) -> int:
    return db.scalar(select(func.count()).select_from(modelo))


IDS_HISTORIAL_P1 = [505, 501, 500, 502, 503, 504]


# =========================================================
# Historial de pagos (GET /pagos/mis-pagos)
# =========================================================


def test_01_historial_de_paciente_con_pagos(cliente_historial_pagos):
    respuesta = cliente_historial_pagos.get(
        "/pagos/mis-pagos", headers=token(11)
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert [item["pago_id"] for item in cuerpo] == IDS_HISTORIAL_P1

    pago500 = next(item for item in cuerpo if item["pago_id"] == 500)
    assert pago500["consulta_clinica_id"] == 100
    assert float(pago500["monto"]) == 210.0
    assert pago500["moneda"] == "BOB"
    assert pago500["metodo_pago"] == "TARJETA"
    assert pago500["estado_pago"] == "APROBADO"
    assert pago500["pasarela"] == "STRIPE"
    assert pago500["fecha_hora_pago"].startswith("2026-10-05T18:30")
    ids_servicios = sorted(s["servicio_realizado_id"] for s in pago500["servicios"])
    assert ids_servicios == [1000, 1001]
    assert {
        s["nombre_servicio"] for s in pago500["servicios"]
    } == {"Consulta General Oftalmológica", "Medición de Lentes"}
    assert all(
        float(s["monto_aplicado"]) > 0 for s in pago500["servicios"]
    )


def test_02_historial_vacio(cliente_historial_pagos):
    respuesta = cliente_historial_pagos.get(
        "/pagos/mis-pagos", headers=token(15)
    )
    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_03_historial_con_pagos_de_distintos_estados(cliente_historial_pagos):
    respuesta = cliente_historial_pagos.get(
        "/pagos/mis-pagos", headers=token(11)
    )
    estados = {item["estado_pago"] for item in respuesta.json()}
    assert estados == {
        "APROBADO",
        "PENDIENTE",
        "RECHAZADO",
        "ANULADO",
        "REEMBOLSADO",
    }


def test_04_historial_no_expone_pagos_de_otro_paciente(cliente_historial_pagos):
    respuesta_p1 = cliente_historial_pagos.get(
        "/pagos/mis-pagos", headers=token(11)
    )
    ids_p1 = {item["pago_id"] for item in respuesta_p1.json()}
    assert 600 not in ids_p1
    assert 700 not in ids_p1
    assert "pi_600" not in respuesta_p1.text

    respuesta_p2 = cliente_historial_pagos.get(
        "/pagos/mis-pagos", headers=token(12)
    )
    assert [item["pago_id"] for item in respuesta_p2.json()] == [600]


def test_12_historial_sin_jwt(cliente_historial_pagos):
    respuesta = cliente_historial_pagos.get("/pagos/mis-pagos")
    assert respuesta.status_code in (401, 403)


def test_13_historial_con_jwt_invalido(cliente_historial_pagos):
    respuesta = cliente_historial_pagos.get(
        "/pagos/mis-pagos",
        headers={"Authorization": "Bearer token.invalido.falso"},
    )
    assert respuesta.status_code in (401, 403)


def test_24_historial_orden_descendente_consistente(cliente_historial_pagos):
    cuerpo = cliente_historial_pagos.get(
        "/pagos/mis-pagos", headers=token(11)
    ).json()
    fechas = [item["fecha_creacion"] for item in cuerpo]
    assert fechas == sorted(fechas, reverse=True)
    assert [item["pago_id"] for item in cuerpo] == IDS_HISTORIAL_P1


# =========================================================
# Comprobante PDF (GET /pagos/mis-pagos/{pago_id}/comprobante)
# =========================================================


def _descargar(cliente, pago_id: int, usuario_id: int = 11):
    return cliente.get(
        f"/pagos/mis-pagos/{pago_id}/comprobante",
        headers=token(usuario_id),
    )


def test_05_comprobante_de_pago_aprobado(cliente_historial_pagos):
    respuesta = _descargar(cliente_historial_pagos, 500)
    assert respuesta.status_code == 200
    assert respuesta.content[:4] == b"%PDF"


@pytest.mark.parametrize(
    "pago_id,estado",
    [
        (501, "PENDIENTE"),
        (502, "RECHAZADO"),
        (503, "ANULADO"),
        (504, "REEMBOLSADO"),
    ],
)
def test_06_a_09_rechazo_de_estados_sin_comprobante(
    cliente_historial_pagos, pago_id, estado
):
    respuesta = _descargar(cliente_historial_pagos, pago_id)
    assert respuesta.status_code == 409
    assert estado in respuesta.text


def test_10_pago_inexistente(cliente_historial_pagos):
    respuesta = _descargar(cliente_historial_pagos, 9999)
    assert respuesta.status_code == 404


def test_11_pago_perteneciente_a_otro_paciente(cliente_historial_pagos):
    ajeno = _descargar(cliente_historial_pagos, 600, usuario_id=11)
    assert ajeno.status_code == 404

    propio = _descargar(cliente_historial_pagos, 600, usuario_id=12)
    assert propio.status_code == 200

    cruce = _descargar(cliente_historial_pagos, 500, usuario_id=12)
    assert cruce.status_code == 404


def test_12b_comprobante_sin_jwt(cliente_historial_pagos):
    respuesta = cliente_historial_pagos.get(
        "/pagos/mis-pagos/500/comprobante"
    )
    assert respuesta.status_code in (401, 403)


def test_12c_comprobante_con_jwt_invalido(cliente_historial_pagos):
    respuesta = cliente_historial_pagos.get(
        "/pagos/mis-pagos/500/comprobante",
        headers={"Authorization": "Bearer token.invalido.falso"},
    )
    assert respuesta.status_code in (401, 403)


def test_14_pdf_content_type_y_nombre_archivo(cliente_historial_pagos):
    respuesta = _descargar(cliente_historial_pagos, 500)
    assert respuesta.headers["content-type"].startswith("application/pdf")
    disposicion = respuesta.headers["content-disposition"]
    assert "comprobante_pago_500.pdf" in disposicion


def test_15_pdf_encabezado_pdf(cliente_historial_pagos):
    contenido = _descargar(cliente_historial_pagos, 500).content
    assert contenido.startswith(b"%PDF")


def test_16_pdf_usa_precios_originales_de_pago_detalle(cliente_historial_pagos):
    texto = _texto_pdf(_descargar(cliente_historial_pagos, 500).content)
    assert "150.00" in texto
    assert "60.00" in texto
    # El precio actual del catalogo (999.00) NO debe aparecer.
    assert "999.00" not in texto


def test_17_pdf_monto_total_correcto(cliente_historial_pagos):
    texto = _texto_pdf(_descargar(cliente_historial_pagos, 500).content)
    assert "TOTAL" in texto
    assert "210.00" in texto


def test_18_pdf_fecha_de_pago_correcta_en_horario_bolivia(
    cliente_historial_pagos,
):
    texto = _texto_pdf(_descargar(cliente_historial_pagos, 500).content)
    # 2026-10-05 18:30 UTC -> 2026-10-05 14:30 America/La_Paz
    assert "05/10/2026" in texto
    assert "14:30" in texto


def test_19_pdf_con_varios_servicios(cliente_historial_pagos):
    texto = _texto_pdf(_descargar(cliente_historial_pagos, 505).content)
    assert "Consulta General Oftalmológica" in texto
    assert "Medición de Lentes" in texto
    assert "240.00" in texto


def test_20_pdf_sin_secretos_de_stripe(cliente_historial_pagos):
    texto = _texto_pdf(_descargar(cliente_historial_pagos, 500).content)
    for prohibido in ["sk_", "sk-", "whsec_", "_secret_", "client_secret"]:
        assert prohibido not in texto


def test_21_pago_con_detalles_de_distintos_pacientes_no_filtra(
    cliente_historial_pagos,
):
    respuesta = _descargar(cliente_historial_pagos, 700)
    assert respuesta.status_code == 404
    assert "Pérez" not in respuesta.text
    assert "pi_700" not in respuesta.text


def test_22_generar_pdf_no_escribe_en_tablas(
    cliente_historial_pagos, db_pagos_historial
):
    tablas = (Pago, PagoDetalle, Bitacora)
    antes = [_contar(db_pagos_historial, tabla) for tabla in tablas]
    assert _descargar(cliente_historial_pagos, 500).status_code == 200
    assert _descargar(cliente_historial_pagos, 505).status_code == 200
    despues = [_contar(db_pagos_historial, tabla) for tabla in tablas]
    assert antes == despues


def test_23_pdf_soporta_nombres_largos_y_acentos(cliente_historial_pagos):
    texto = _texto_pdf(_descargar(cliente_historial_pagos, 505).content)
    assert "Evaluación Integral de Fondo de Ojo" in texto
    assert "Pérez Gutiérrez" in texto
    assert "COMPROBANTE DE PAGO" in texto
    assert "constancia de pago" in texto
