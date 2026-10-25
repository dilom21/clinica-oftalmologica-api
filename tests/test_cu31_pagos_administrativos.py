"""CU31: pagos manuales, PDF, outbox Resend y autorizacion administrativa."""

import base64
import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import func, select, text

from tests.test_base_flujo_pagos import cliente_pagos, db_pagos, token
from app.core import config
from app.modules.gestion_pagos.models.models import NotificacionPago, Pago, PagoDetalle
from app.modules.gestion_pagos.providers.resend_provider import (
    ErrorResend,
    ResendProvider,
    RespuestaResend,
    ResultadoResendDesconocido,
)
from app.modules.gestion_pagos.schemas.administrativos import PagoAdministrativoCrear
from app.modules.gestion_pagos.services import administrativos
from app.modules.gestion_pagos.services import notificaciones
from app.modules.gestion_pagos.services import service as pagos_service
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora, Usuario
from app.modules.gestion_usuarios_seguridad.services import rol_service


@pytest.fixture
def db_cu31(db_pagos):
    ddls = [
        "CREATE TABLE modulo (id INTEGER PRIMARY KEY, nombre TEXT, descripcion TEXT, estado BOOLEAN)",
        "CREATE TABLE funcion (id INTEGER PRIMARY KEY, modulo_id INTEGER, nombre TEXT, descripcion TEXT, estado BOOLEAN)",
        "CREATE TABLE accion (id INTEGER PRIMARY KEY, nombre TEXT, descripcion TEXT, estado BOOLEAN)",
        "CREATE TABLE rol_funcion (id INTEGER PRIMARY KEY, rol_id INTEGER, funcion_id INTEGER, accion_id INTEGER, descripcion TEXT, UNIQUE(rol_id, funcion_id))",
    ]
    for ddl in ddls:
        db_pagos.execute(text(ddl))
    db_pagos.execute(text(
        "INSERT INTO rol VALUES "
        "(3,'Recepcionista',NULL,1,0,'2026-10-01 12:00:00'),"
        "(4,'Oftalmologo',NULL,1,0,'2026-10-01 12:00:00')"
    ))
    db_pagos.execute(text(
        "INSERT INTO usuario VALUES "
        "(15,'recepcion@test','x',1,'2026-10-01 12:00:00',3),"
        "(16,'oftal-cu31@test','x',1,'2026-10-01 12:00:00',4)"
    ))
    db_pagos.execute(text("INSERT INTO modulo VALUES (1,'Pagos',NULL,1)"))
    db_pagos.execute(text(
        "INSERT INTO funcion VALUES "
        "(1,1,'Gestionar pagos',NULL,1),"
        "(2,1,'Confirmar pagos bancarios',NULL,1)"
    ))
    db_pagos.execute(text(
        "INSERT INTO accion VALUES "
        "(1,'LECTURA',NULL,1),(2,'ESCRITURA',NULL,1),(3,'AMBAS',NULL,1)"
    ))
    db_pagos.execute(text(
        "INSERT INTO rol_funcion VALUES "
        "(1,2,1,3,NULL),(2,2,2,3,NULL),"
        "(3,3,1,3,NULL),(4,3,2,2,NULL)"
    ))
    db_pagos.execute(text("UPDATE usuario SET correo='ana@example.com' WHERE id=11"))
    db_pagos.commit()
    return db_pagos


@pytest.fixture
def cliente_cu31(cliente_pagos, db_cu31):
    return cliente_pagos


def _admin(db):
    return db.get(Usuario, 13)


def _recepcionista(db):
    return db.get(Usuario, 15)


def _asignar_permiso(db, rol_id, funcion_id, accion_id=None):
    db.execute(text(
        "DELETE FROM rol_funcion WHERE rol_id=:rol AND funcion_id=:funcion"
    ), {"rol": rol_id, "funcion": funcion_id})
    if accion_id is not None:
        db.execute(text(
            "INSERT INTO rol_funcion "
            "(id,rol_id,funcion_id,accion_id,descripcion) "
            "VALUES (:id,:rol,:funcion,:accion,NULL)"
        ), {
            "id": rol_id * 100 + funcion_id,
            "rol": rol_id,
            "funcion": funcion_id,
            "accion": accion_id,
        })
    db.commit()


def _servicio(db, servicio_id, *, paciente=1, consulta=100, precio="45.50", estado=1):
    db.execute(
        text(
            "INSERT INTO servicio_realizado "
            "(id,servicio_id,paciente_id,consulta_clinica_id,oftalmologo_id,"
            "fecha_realizacion,observaciones,estado,precio_aplicado) "
            "VALUES (:id,1,:paciente,:consulta,5,'2026-10-10',NULL,:estado,:precio)"
        ),
        {"id": servicio_id, "paciente": paciente, "consulta": consulta,
         "estado": estado, "precio": precio},
    )
    db.commit()


def _datos(servicios, metodo="EFECTIVO", referencia=None, paciente=1):
    return PagoAdministrativoCrear(
        paciente_id=paciente,
        servicio_realizado_ids=servicios,
        metodo_pago=metodo,
        referencia_bancaria=referencia,
    )


def test_esquema_no_acepta_monto_y_valida_referencia():
    with pytest.raises(ValueError):
        PagoAdministrativoCrear(
            paciente_id=1, servicio_realizado_ids=[1], metodo_pago="EFECTIVO",
            monto="0.01",
        )
    with pytest.raises(ValueError):
        _datos([1], "TRANSFERENCIA")
    with pytest.raises(ValueError):
        _datos([1], "EFECTIVO", "NO-CORRESPONDE")


def test_esquema_rechaza_servicios_duplicados():
    with pytest.raises(ValueError):
        _datos([1, 1])


def test_lista_servicios_pendientes_y_total_backend(db_cu31):
    _servicio(db_cu31, 1100)
    resultado = administrativos.listar_servicios_pendientes(
        db_cu31, _admin(db_cu31), paciente_id=1, consulta_clinica_id=100,
    )
    assert [item.servicio_realizado_id for item in resultado.servicios] == [1100, 1000]
    assert resultado.total == Decimal("45.50")
    assert resultado.servicios[0].cobrable is True
    assert resultado.servicios[1].conflicto_pago == "PAGO_MANUAL_PENDIENTE"


def test_efectivo_aprobado_atomico_con_bitacora_y_outbox(db_cu31):
    _servicio(db_cu31, 1101, precio="45.50")
    respuesta = administrativos.registrar_pago(
        db_cu31, _datos([1101]), "cash-1101", _admin(db_cu31), "127.0.0.1",
    )
    assert respuesta.estado == "APROBADO"
    assert respuesta.monto == Decimal("45.50")
    assert respuesta.fecha_hora_pago is not None
    assert respuesta.notificacion.estado == "PENDIENTE"
    assert db_cu31.scalar(select(func.count()).select_from(PagoDetalle)) == 5
    assert db_cu31.scalar(select(Bitacora.accion).where(
        Bitacora.id_registro_afectado == respuesta.id,
    )) == "REGISTRAR_PAGO_ADMINISTRATIVO"


@pytest.mark.parametrize("metodo", ["TRANSFERENCIA", "QR"])
def test_bancario_nace_pendiente_y_confirmacion_es_idempotente(db_cu31, metodo):
    servicio_id = 1200 if metodo == "TRANSFERENCIA" else 1201
    _servicio(db_cu31, servicio_id)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([servicio_id], metodo, f"REF-{servicio_id}"),
        f"idem-{servicio_id}", _admin(db_cu31),
    )
    assert pago.estado == "PENDIENTE"
    assert pago.notificacion is None
    confirmado = administrativos.confirmar_pago(db_cu31, pago.id, _admin(db_cu31))
    repetido = administrativos.confirmar_pago(db_cu31, pago.id, _admin(db_cu31))
    assert confirmado.estado == repetido.estado == "APROBADO"
    assert db_cu31.scalar(select(func.count()).select_from(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    )) == 1


def test_reintento_http_devuelve_mismo_pago_sin_duplicar(db_cu31):
    _servicio(db_cu31, 1300)
    primero = administrativos.registrar_pago(
        db_cu31, _datos([1300]), "retry-1300", _admin(db_cu31),
    )
    segundo = administrativos.registrar_pago(
        db_cu31, _datos([1300]), "retry-1300", _admin(db_cu31),
    )
    assert segundo.id == primero.id
    assert db_cu31.scalar(select(func.count()).select_from(Pago).where(
        Pago.clave_idempotencia == "retry-1300",
    )) == 1


def test_clave_idempotente_con_otro_payload_rechaza(db_cu31):
    _servicio(db_cu31, 1301)
    _servicio(db_cu31, 1302)
    administrativos.registrar_pago(
        db_cu31, _datos([1301]), "retry-conflict", _admin(db_cu31),
    )
    with pytest.raises(HTTPException) as exc:
        administrativos.registrar_pago(
            db_cu31, _datos([1302]), "retry-conflict", _admin(db_cu31),
        )
    assert exc.value.status_code == 409


def test_referencia_bancaria_duplicada_rechaza(db_cu31):
    _servicio(db_cu31, 1401)
    _servicio(db_cu31, 1402)
    administrativos.registrar_pago(
        db_cu31, _datos([1401], "QR", "BANCO-001"), "qr-1401", _admin(db_cu31),
    )
    with pytest.raises(HTTPException) as exc:
        administrativos.registrar_pago(
            db_cu31, _datos([1402], "QR", "BANCO-001"), "qr-1402", _admin(db_cu31),
        )
    assert exc.value.status_code == 409


def test_intento_stripe_pendiente_bloquea_pago_manual(db_cu31):
    db_cu31.execute(text("UPDATE pago SET pasarela='STRIPE' WHERE id=501"))
    db_cu31.commit()
    with pytest.raises(HTTPException) as exc:
        administrativos.registrar_pago(
            db_cu31, _datos([1000]), "stripe-conflict", _admin(db_cu31),
        )
    assert exc.value.status_code == 409
    assert "Stripe" in exc.value.detail


def test_servicios_de_pacientes_distintos_rechazados(db_cu31):
    _servicio(db_cu31, 1501)
    _servicio(db_cu31, 1502, paciente=2, consulta=200)
    with pytest.raises(HTTPException) as exc:
        administrativos.registrar_pago(
            db_cu31, _datos([1501, 1502]), "mixed-patients", _admin(db_cu31),
        )
    assert exc.value.status_code == 422


def test_servicio_de_otro_paciente_rechazado(db_cu31):
    _servicio(db_cu31, 1550, paciente=2, consulta=200)
    with pytest.raises(HTTPException) as exc:
        administrativos.registrar_pago(
            db_cu31, _datos([1550]), "wrong-patient", _admin(db_cu31),
        )
    assert exc.value.status_code == 403


def test_servicio_inactivo_rechazado(db_cu31):
    _servicio(db_cu31, 1551, estado=0)
    with pytest.raises(HTTPException) as exc:
        administrativos.registrar_pago(
            db_cu31, _datos([1551]), "inactive-service", _admin(db_cu31),
        )
    assert exc.value.status_code == 422


def test_servicio_ya_pagado_rechazado(db_cu31):
    with pytest.raises(HTTPException) as exc:
        administrativos.registrar_pago(
            db_cu31, _datos([1001]), "already-paid", _admin(db_cu31),
        )
    assert exc.value.status_code == 409


@pytest.mark.parametrize("precio", [None, "0", "-1"])
def test_precio_inexistente_o_invalido_rechazado(db_cu31, precio):
    _servicio(db_cu31, 1600, precio=precio)
    with pytest.raises(HTTPException) as exc:
        administrativos.registrar_pago(
            db_cu31, _datos([1600]), f"bad-price-{precio}", _admin(db_cu31),
        )
    assert exc.value.status_code == 422


def test_fallo_bitacora_revierte_pago_y_outbox(db_cu31, monkeypatch):
    _servicio(db_cu31, 1700)
    def falla(**_kwargs):
        raise RuntimeError("fallo controlado")
    monkeypatch.setattr(administrativos, "registrar_bitacora", falla)
    with pytest.raises(RuntimeError):
        administrativos.registrar_pago(
            db_cu31, _datos([1700]), "rollback-1700", _admin(db_cu31),
        )
    assert db_cu31.scalar(select(Pago).where(Pago.clave_idempotencia == "rollback-1700")) is None
    assert db_cu31.scalar(select(func.count()).select_from(NotificacionPago)) == 0


def test_endpoints_exigen_token_rol_y_permiso(cliente_cu31, db_cu31):
    _servicio(db_cu31, 1800)
    url = "/pagos/administrativos"
    body = {"paciente_id": 1, "servicio_realizado_ids": [1800], "metodo_pago": "EFECTIVO"}
    assert cliente_cu31.post(url, json=body).status_code in {401, 403}
    assert cliente_cu31.post(url, json=body, headers={
        **token(11), "Idempotency-Key": "patient-denied",
    }).status_code == 403
    ok = cliente_cu31.post(url, json=body, headers={
        **token(13, 2), "Idempotency-Key": "admin-allowed",
    })
    assert ok.status_code == 201


def test_recepcionista_con_permiso_registra_pago(cliente_cu31, db_cu31):
    _servicio(db_cu31, 1802)
    respuesta = cliente_cu31.post(
        "/pagos/administrativos",
        json={"paciente_id": 1, "servicio_realizado_ids": [1802],
              "metodo_pago": "EFECTIVO"},
        headers={**token(15, 3), "Idempotency-Key": "reception-allowed"},
    )
    assert respuesta.status_code == 201


@pytest.mark.parametrize("usuario_id,rol_id", [(13, 2), (15, 3)])
def test_actor_administrativo_sin_permiso_recibe_403(
    cliente_cu31, db_cu31, usuario_id, rol_id,
):
    _asignar_permiso(db_cu31, rol_id, 1, None)
    respuesta = cliente_cu31.get(
        "/pagos/administrativos/servicios-pendientes?paciente_id=1",
        headers=token(usuario_id, rol_id),
    )
    assert respuesta.status_code == 403


@pytest.mark.parametrize(
    ("accion_id", "lectura_esperada", "escritura_esperada"),
    [(1, 200, 403), (2, 403, 201), (3, 200, 201)],
)
def test_acciones_rbac_se_respetan_independientemente(
    cliente_cu31,
    db_cu31,
    accion_id,
    lectura_esperada,
    escritura_esperada,
):
    _asignar_permiso(db_cu31, 3, 1, accion_id)
    servicio_id = 1810 + accion_id
    _servicio(db_cu31, servicio_id)
    lectura = cliente_cu31.get(
        "/pagos/administrativos/servicios-pendientes?paciente_id=1",
        headers=token(15, 3),
    )
    escritura = cliente_cu31.post(
        "/pagos/administrativos",
        json={"paciente_id": 1, "servicio_realizado_ids": [servicio_id],
              "metodo_pago": "EFECTIVO"},
        headers={
            **token(15, 3),
            "Idempotency-Key": f"action-matrix-{accion_id}",
        },
    )
    assert lectura.status_code == lectura_esperada
    assert escritura.status_code == escritura_esperada


def test_confirmar_exige_permiso_especifico_y_no_gestionar_pagos(
    cliente_cu31, db_cu31,
):
    _servicio(db_cu31, 1820)
    creado = cliente_cu31.post(
        "/pagos/administrativos",
        json={"paciente_id": 1, "servicio_realizado_ids": [1820],
              "metodo_pago": "TRANSFERENCIA",
              "referencia_bancaria": "BANK-1820"},
        headers={**token(13, 2), "Idempotency-Key": "bank-specific"},
    )
    assert creado.status_code == 201
    pago_id = creado.json()["id"]

    _asignar_permiso(db_cu31, 2, 2, None)
    denegado = cliente_cu31.post(
        f"/pagos/administrativos/{pago_id}/confirmar",
        headers=token(13, 2),
    )
    assert denegado.status_code == 403

    _asignar_permiso(db_cu31, 2, 2, 2)
    permitido = cliente_cu31.post(
        f"/pagos/administrativos/{pago_id}/confirmar",
        headers=token(13, 2),
    )
    assert permitido.status_code == 200


def test_confirmacion_recepcionista_exige_escritura_o_ambas(
    cliente_cu31, db_cu31,
):
    _servicio(db_cu31, 1821)
    creado = cliente_cu31.post(
        "/pagos/administrativos",
        json={"paciente_id": 1, "servicio_realizado_ids": [1821],
              "metodo_pago": "QR", "referencia_bancaria": "QR-1821"},
        headers={**token(13, 2), "Idempotency-Key": "qr-reception-confirm"},
    )
    pago_id = creado.json()["id"]

    _asignar_permiso(db_cu31, 3, 2, 1)
    solo_lectura = cliente_cu31.post(
        f"/pagos/administrativos/{pago_id}/confirmar",
        headers=token(15, 3),
    )
    assert solo_lectura.status_code == 403

    _asignar_permiso(db_cu31, 3, 2, 2)
    escritura = cliente_cu31.post(
        f"/pagos/administrativos/{pago_id}/confirmar",
        headers=token(15, 3),
    )
    assert escritura.status_code == 200


def test_oftalmologo_no_opera_aunque_reciba_permiso_por_error(
    cliente_cu31, db_cu31,
):
    _asignar_permiso(db_cu31, 4, 1, 3)
    respuesta = cliente_cu31.get(
        "/pagos/administrativos/servicios-pendientes?paciente_id=1",
        headers=token(16, 4),
    )
    assert respuesta.status_code == 403


def test_cambios_cu05_se_aplican_en_la_siguiente_peticion(
    cliente_cu31, db_cu31,
):
    _servicio(db_cu31, 1830)
    _servicio(db_cu31, 1831)
    permitido = cliente_cu31.post(
        "/pagos/administrativos",
        json={"paciente_id": 1, "servicio_realizado_ids": [1830],
              "metodo_pago": "EFECTIVO"},
        headers={**token(13, 2), "Idempotency-Key": "before-cu05-change"},
    )
    assert permitido.status_code == 201

    _asignar_permiso(db_cu31, 2, 1, 1)
    denegado = cliente_cu31.post(
        "/pagos/administrativos",
        json={"paciente_id": 1, "servicio_realizado_ids": [1831],
              "metodo_pago": "EFECTIVO"},
        headers={**token(13, 2), "Idempotency-Key": "after-cu05-change"},
    )
    assert denegado.status_code == 403


def test_menu_dinamico_expone_solo_las_dos_funciones_cu31(
    cliente_cu31,
):
    respuesta = cliente_cu31.get("/seguridad/menu", headers=token(15, 3))
    assert respuesta.status_code == 200
    pagos = next(item for item in respuesta.json() if item["nombre"] == "Pagos")
    assert {
        (item["nombre"], item["accion_nombre"])
        for item in pagos["funciones"]
    } == {
        ("Gestionar pagos", "AMBAS"),
        ("Confirmar pagos bancarios", "ESCRITURA"),
    }


def test_catalogo_cu05_expone_funciones_bajo_modulo_pagos(db_cu31):
    modulos = rol_service.listar_modulos_con_funciones(db_cu31)
    pagos = next(item for item in modulos if item.nombre == "Pagos")
    assert {item.nombre for item in pagos.funciones} == {
        "Gestionar pagos",
        "Confirmar pagos bancarios",
    }


def test_migracion_rbac_preserva_revocaciones_y_no_duplica_funciones():
    sql = (
        Path(__file__).resolve().parents[1]
        / "db" / "cu31_pagos_administrativos_resend.sql"
    ).read_text(encoding="utf-8")
    assert "lower(trim(nombre)) = 'pagos'" in sql
    assert sql.count("'Gestionar pagos'") >= 2
    assert sql.count("'Confirmar pagos bancarios'") >= 2
    assert "v_gestion_nueva" in sql
    assert "v_confirmar_nueva" in sql
    assert "ON CONFLICT (rol_id, funcion_id) DO NOTHING" in sql
    assert "DELETE FROM public.rol_funcion" not in sql
    assert "ON CONFLICT (rol_id, funcion_id) DO UPDATE" not in sql


def test_migracion_protege_outbox_y_conserva_evidencia_de_notificacion():
    raiz = Path(__file__).resolve().parents[1]
    sql = (raiz / "db" / "cu31_pagos_administrativos_resend.sql").read_text(
        encoding="utf-8",
    )
    assert "ON DELETE RESTRICT" in sql
    assert "ON DELETE CASCADE" not in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "NO FORCE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL PRIVILEGES ON TABLE public.notificacion_pago FROM PUBLIC" in sql
    assert "rolname IN ('anon', 'authenticated')" in sql
    assert "has_sequence_privilege" in sql
    assert "v2-cu31" in sql
    fk = next(iter(NotificacionPago.__table__.c.pago_id.foreign_keys))
    assert fk.ondelete == "RESTRICT"


def test_ensayo_cu31_solo_admite_base_test_y_reemplaza_commit_en_memoria():
    helper = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "db" / "test_cu31_migration_rollback.ps1"
    ).read_text(encoding="utf-8")
    assert "_(test|testing)" in helper
    assert "supabase" in helper.lower()
    assert "$commitMatches.Count -ne 1" in helper
    assert "$commitRegex.Replace($migrationSql, 'ROLLBACK;', 1)" in helper
    assert "ON_ERROR_STOP=1" in helper


def test_idempotency_key_invalida_rechazada(cliente_cu31):
    respuesta = cliente_cu31.post(
        "/pagos/administrativos",
        json={"paciente_id": 1, "servicio_realizado_ids": [1000], "metodo_pago": "EFECTIVO"},
        headers={**token(13, 2), "Idempotency-Key": "corta"},
    )
    assert respuesta.status_code == 422


def test_autoridad_final_del_service_rechaza_paciente(db_cu31):
    _servicio(db_cu31, 1801)
    with pytest.raises(HTTPException) as exc:
        administrativos.registrar_pago(
            db_cu31, _datos([1801]), "service-denied", db_cu31.get(Usuario, 11),
        )
    assert exc.value.status_code == 403


def test_endpoint_rechaza_monto_controlado_por_cliente(cliente_cu31):
    respuesta = cliente_cu31.post(
        "/pagos/administrativos",
        json={"paciente_id": 1, "servicio_realizado_ids": [1000],
              "metodo_pago": "EFECTIVO", "monto": "0.01"},
        headers={**token(13, 2), "Idempotency-Key": "manipulated-total"},
    )
    assert respuesta.status_code == 422


def test_endpoint_rechaza_seleccion_arbitraria_de_base(cliente_cu31):
    respuesta = cliente_cu31.post(
        "/pagos/administrativos",
        json={"paciente_id": 1, "servicio_realizado_ids": [1000],
              "metodo_pago": "EFECTIVO", "database_name": "tenant_ajeno"},
        headers={**token(13, 2), "Idempotency-Key": "database-forbidden"},
    )
    assert respuesta.status_code == 422


def test_listado_detalle_y_pdf_administrativo(cliente_cu31, db_cu31):
    _contexto_tenant(
        db_cu31,
        nombre="Centro Oftalmologico Vision Clara",
    )
    _servicio(db_cu31, 1900)
    creado = cliente_cu31.post(
        "/pagos/administrativos",
        json={"paciente_id": 1, "servicio_realizado_ids": [1900], "metodo_pago": "EFECTIVO"},
        headers={**token(13, 2), "Idempotency-Key": "list-pdf-1900"},
    ).json()
    listado = cliente_cu31.get(
        "/pagos/administrativos?paciente_id=1&estado=APROBADO",
        headers=token(13, 2),
    )
    assert listado.status_code == 200
    assert any(item["id"] == creado["id"] for item in listado.json()["items"])
    pdf = cliente_cu31.get(
        f"/pagos/administrativos/{creado['id']}/comprobante",
        headers=token(13, 2),
    )
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    texto_pdf = _texto_pdf(pdf.content)
    assert "CENTRO OFTALMOLOGICO VISION CLARA" in texto_pdf
    assert "COMPROBANTE DE PAGO" in texto_pdf


def test_pdf_del_mismo_pago_es_binariamente_estable_y_descargar_no_envia(
    db_cu31,
):
    _servicio(db_cu31, 1905)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([1905]), "pdf-stable-1905", _admin(db_cu31),
    )
    cantidad_inicial = db_cu31.scalar(
        select(func.count()).select_from(NotificacionPago),
    )

    primero, nombre_primero = administrativos.generar_comprobante(
        db_cu31, pago.id, _admin(db_cu31),
    )
    segundo, nombre_segundo = administrativos.generar_comprobante(
        db_cu31, pago.id, _admin(db_cu31),
    )

    assert primero == segundo
    assert nombre_primero == nombre_segundo
    assert db_cu31.scalar(
        select(func.count()).select_from(NotificacionPago),
    ) == cantidad_inicial


def test_pdf_usa_nombre_oficial_de_otro_tenant_sin_mezclar_clinicas(db_cu31):
    _contexto_tenant(
        db_cu31,
        codigo="OFTALMO-NORTE",
        nombre="Clinica Oftalmo Norte",
    )
    _servicio(db_cu31, 1901)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([1901]), "pdf-tenant-norte", _admin(db_cu31),
    )

    contenido, _ = administrativos.generar_comprobante(
        db_cu31, pago.id, _admin(db_cu31),
    )
    texto_pdf = _texto_pdf(contenido)

    assert "CLINICA OFTALMO NORTE" in texto_pdf
    assert "VISION CLARA" not in texto_pdf


def test_pdf_admite_nombre_largo_de_clinica(db_cu31):
    nombre = (
        "Centro Universitario Especializado de Diagnostico y Tratamiento "
        "Oftalmologico Integral del Oriente Boliviano"
    )
    _contexto_tenant(db_cu31, nombre=nombre)
    _servicio(db_cu31, 1902)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([1902]), "pdf-long-name", _admin(db_cu31),
    )

    contenido, _ = administrativos.generar_comprobante(
        db_cu31, pago.id, _admin(db_cu31),
    )
    lector = PdfReader(BytesIO(contenido))
    texto_pdf = " ".join(
        " ".join((pagina.extract_text() or "").split())
        for pagina in lector.pages
    )

    assert nombre.upper() in texto_pdf
    assert "COMPROBANTE DE PAGO" in texto_pdf
    assert len(lector.pages) == 1


def test_pdf_legacy_sin_contexto_conserva_encabezado_generico(db_cu31):
    assert db_cu31.info.get("tenant_context") is None
    _servicio(db_cu31, 1903)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([1903]), "pdf-legacy", _admin(db_cu31),
    )

    contenido, _ = administrativos.generar_comprobante(
        db_cu31, pago.id, _admin(db_cu31),
    )

    lector = PdfReader(BytesIO(contenido))
    assert lector.metadata.author == "CLÍNICA OFTALMOLÓGICA"
    assert "COMPROBANTE DE PAGO" in _texto_pdf(contenido)


def test_pdf_tenant_falla_seguro_si_falta_nombre_oficial(db_cu31):
    _contexto_tenant(db_cu31, nombre=None)
    _servicio(db_cu31, 1904)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([1904]), "pdf-missing-name", _admin(db_cu31),
    )

    with pytest.raises(HTTPException) as exc:
        administrativos.generar_comprobante(db_cu31, pago.id, _admin(db_cu31))

    assert exc.value.status_code == 500
    assert "resolver con seguridad" in exc.value.detail


def test_pdf_tenant_falla_seguro_si_base_no_coincide():
    contexto = SimpleNamespace(
        empresa_id=70,
        empresa_codigo="VISION-CLARA",
        tenant_database_id=701,
        empresa_nombre="Centro Oftalmologico Vision Clara",
        database_name="tenant_vision_clara",
    )

    class _DbConBaseDistinta:
        info = {"tenant_context": contexto}

        @staticmethod
        def get_bind():
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        @staticmethod
        def scalar(_consulta):
            return "tenant_ajena"

    with pytest.raises(HTTPException) as exc:
        pagos_service._nombre_clinica_comprobante(_DbConBaseDistinta())

    assert exc.value.status_code == 500
    assert "no coincide" in exc.value.detail


def test_pdf_pendiente_rechazado(db_cu31):
    _servicio(db_cu31, 1950)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([1950], "QR", "PEND-1950"), "pending-pdf", _admin(db_cu31),
    )
    with pytest.raises(HTTPException) as exc:
        administrativos.generar_comprobante(db_cu31, pago.id, _admin(db_cu31))
    assert exc.value.status_code == 409


def test_pendiente_no_programa_correo(db_cu31):
    _servicio(db_cu31, 1951)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([1951], "TRANSFERENCIA", "PEND-1951"),
        "pending-no-email", _admin(db_cu31),
    )
    assert db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    )) is None


def test_paciente_solo_consulta_estado_notificacion_propio(cliente_cu31, db_cu31):
    _servicio(db_cu31, 1960)
    propio = administrativos.registrar_pago(
        db_cu31, _datos([1960]), "notif-own", _admin(db_cu31),
    )
    _servicio(db_cu31, 2960, paciente=2, consulta=200)
    ajeno = administrativos.registrar_pago(
        db_cu31, _datos([2960], paciente=2), "notif-other", _admin(db_cu31),
    )
    assert cliente_cu31.get(
        f"/pagos/{propio.id}/notificacion", headers=token(11),
    ).status_code == 200
    assert cliente_cu31.get(
        f"/pagos/{ajeno.id}/notificacion", headers=token(11),
    ).status_code == 404


class _ResendExitoso:
    def __init__(self):
        self.llamadas = []

    def enviar_comprobante(self, **kwargs):
        self.llamadas.append(kwargs)
        return RespuestaResend(id="email_123")


def _activar_destinatario_pruebas(
    monkeypatch,
    *,
    correo="haroldresend@gmail.com",
):
    monkeypatch.setattr(config, "EMAIL_TEST", correo)
    for nombre in (
        "RENDER", "RENDER_SERVICE_ID", "RENDER_EXTERNAL_HOSTNAME",
        "APP_ENV", "ENVIRONMENT", "ENV",
    ):
        monkeypatch.delenv(nombre, raising=False)


def _contexto_tenant(
    db,
    codigo="VISION-CLARA",
    nombre="Centro Oftalmologico Vision Clara",
    database_name="tenant_test",
):
    db.info["tenant_context"] = SimpleNamespace(
        empresa_id=70,
        empresa_codigo=codigo,
        tenant_database_id=701,
        empresa_nombre=nombre,
        database_name=database_name,
    )


def _texto_pdf(contenido: bytes) -> str:
    return "\n".join(
        pagina.extract_text() or ""
        for pagina in PdfReader(BytesIO(contenido)).pages
    )


def test_worker_adjunta_pdf_destinatario_real_y_aceptado(db_cu31, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_TEST", "")
    _servicio(db_cu31, 2001)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2001]), "email-2001", _admin(db_cu31),
    )
    proveedor = _ResendExitoso()
    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True
    assert proveedor.llamadas[0]["destinatario"] == "ana@example.com"
    assert proveedor.llamadas[0]["pdf"].startswith(b"%PDF")
    item = db_cu31.scalar(select(NotificacionPago).where(NotificacionPago.pago_id == pago.id))
    assert item.estado == "ACEPTADO_POR_PROVEEDOR"
    assert item.proveedor_mensaje_id == "email_123"


def test_modo_pruebas_inactivo_conserva_destinatario_original(
    db_cu31, monkeypatch,
):
    monkeypatch.setattr(config, "EMAIL_TEST", "")
    _contexto_tenant(db_cu31)
    _servicio(db_cu31, 2010)

    pago = administrativos.registrar_pago(
        db_cu31, _datos([2010]), "email-normal-2010", _admin(db_cu31),
    )

    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert item.destinatario == "ana@example.com"
    assert item.ultimo_error is None


@pytest.mark.parametrize("correo_original", ["", "correo-invalido"])
def test_modo_pruebas_redirige_correo_ausente_o_invalido_sin_reintento(
    db_cu31, monkeypatch, correo_original,
):
    _activar_destinatario_pruebas(monkeypatch)
    _contexto_tenant(db_cu31)
    db_cu31.execute(
        text("UPDATE usuario SET correo=:correo WHERE id=11"),
        {"correo": correo_original},
    )
    db_cu31.commit()
    servicio_id = 2011 if correo_original == "" else 2012
    _servicio(db_cu31, servicio_id)

    pago = administrativos.registrar_pago(
        db_cu31,
        _datos([servicio_id]),
        f"email-test-{servicio_id}",
        _admin(db_cu31),
    )

    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert item.estado == "PENDIENTE"
    assert item.destinatario == "haroldresend@gmail.com"
    assert item.ultimo_error is None
    assert item.intentos == 0


def test_worker_registra_redireccion_en_logs_y_no_como_error(
    db_cu31, monkeypatch, caplog,
):
    _activar_destinatario_pruebas(monkeypatch)
    _contexto_tenant(db_cu31)
    _servicio(db_cu31, 2013)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2013]), "email-test-2013", _admin(db_cu31),
    )
    proveedor = _ResendExitoso()

    with caplog.at_level("WARNING"):
        assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True

    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert proveedor.llamadas[0]["destinatario"] == "haroldresend@gmail.com"
    assert proveedor.llamadas[0]["pdf"].startswith(b"%PDF")
    assert "CENTRO OFTALMOLOGICO VISION CLARA" in _texto_pdf(
        proveedor.llamadas[0]["pdf"],
    )
    assert item.estado == "ACEPTADO_POR_PROVEEDOR"
    assert item.ultimo_error is None
    assert "no representa entrega al paciente" in caplog.text
    assert any(
        getattr(record, "evento", None) == "CU31_EMAIL_TEST_REDIRECT"
        and getattr(record, "redireccion_email_test", False) is True
        for record in caplog.records
    )


def test_email_test_aplica_a_pacientes_de_cualquier_tenant(
    db_cu31, monkeypatch,
):
    _activar_destinatario_pruebas(monkeypatch)
    _contexto_tenant(db_cu31, "OTRA-CLINICA")
    _servicio(db_cu31, 2014)
    pago_otro_tenant = administrativos.registrar_pago(
        db_cu31, _datos([2014]), "email-other-tenant", _admin(db_cu31),
    )
    item_otro_tenant = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago_otro_tenant.id,
    ))
    assert item_otro_tenant.destinatario == "haroldresend@gmail.com"
    assert item_otro_tenant.ultimo_error is None

    _contexto_tenant(db_cu31, "VISION-CLARA")
    db_cu31.execute(text(
        "UPDATE usuario SET correo='beto@example.com' WHERE id=12"
    ))
    db_cu31.commit()
    _servicio(db_cu31, 2015, paciente=2, consulta=200)
    pago_otro_paciente = administrativos.registrar_pago(
        db_cu31,
        _datos([2015], paciente=2),
        "email-other-patient",
        _admin(db_cu31),
    )
    item_otro_paciente = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago_otro_paciente.id,
    ))
    assert item_otro_paciente.destinatario == "haroldresend@gmail.com"
    assert item_otro_paciente.ultimo_error is None


def test_worker_en_modo_pruebas_procesa_otro_paciente_del_tenant(
    db_cu31, monkeypatch,
):
    _activar_destinatario_pruebas(monkeypatch)
    _contexto_tenant(db_cu31)
    db_cu31.execute(text(
        "UPDATE usuario SET correo='beto@example.com' WHERE id=12"
    ))
    db_cu31.commit()
    _servicio(db_cu31, 2018, paciente=2, consulta=200)
    pago = administrativos.registrar_pago(
        db_cu31,
        _datos([2018], paciente=2),
        "email-no-autorizado-2018",
        _admin(db_cu31),
    )
    proveedor = _ResendExitoso()

    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True
    assert proveedor.llamadas[0]["destinatario"] == "haroldresend@gmail.com"
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert item.estado == "ACEPTADO_POR_PROVEEDOR"
    assert item.intentos == 1
    assert item.ultimo_error is None


def test_worker_congela_destinatario_de_notificacion_pendiente_preexistente(
    db_cu31, monkeypatch,
):
    monkeypatch.setattr(config, "EMAIL_TEST", "")
    _contexto_tenant(db_cu31, "VISION-CLARA")
    _servicio(db_cu31, 2020)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2020]), "email-preexistente-2020", _admin(db_cu31),
    )
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert item.destinatario == "ana@example.com"

    _activar_destinatario_pruebas(monkeypatch)
    proveedor = _ResendExitoso()
    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True

    db_cu31.refresh(item)
    assert proveedor.llamadas[0]["destinatario"] == "ana@example.com"
    assert item.destinatario == "ana@example.com"
    assert item.ultimo_error is None


def test_email_test_funciona_sin_contexto_tenant_legacy(
    db_cu31, monkeypatch,
):
    _activar_destinatario_pruebas(monkeypatch)
    _servicio(db_cu31, 2021)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2021]), "email-legacy-2021", _admin(db_cu31),
    )
    proveedor = _ResendExitoso()

    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True
    assert proveedor.llamadas[0]["destinatario"] == "haroldresend@gmail.com"
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert item.ultimo_error is None


def test_worker_en_modo_pruebas_procesa_otro_tenant_sin_filtros(
    db_cu31, monkeypatch,
):
    _activar_destinatario_pruebas(monkeypatch)
    _contexto_tenant(db_cu31, "OTRA-CLINICA")
    _servicio(db_cu31, 2019)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2019]), "email-tenant-no-autorizado", _admin(db_cu31),
    )
    proveedor = _ResendExitoso()

    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True
    assert proveedor.llamadas[0]["destinatario"] == "haroldresend@gmail.com"
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert item.estado == "ACEPTADO_POR_PROVEEDOR"
    assert item.intentos == 1
    assert item.ultimo_error is None


@pytest.mark.parametrize("correo_original", ["", "correo-invalido"])
def test_modo_pruebas_inactivo_no_oculta_destinatario_no_disponible(
    db_cu31, monkeypatch, correo_original,
):
    monkeypatch.setattr(config, "EMAIL_TEST", "")
    _contexto_tenant(db_cu31)
    db_cu31.execute(
        text("UPDATE usuario SET correo=:correo WHERE id=11"),
        {"correo": correo_original},
    )
    db_cu31.commit()
    servicio_id = 2016 if correo_original == "" else 2017
    _servicio(db_cu31, servicio_id)

    pago = administrativos.registrar_pago(
        db_cu31,
        _datos([servicio_id]),
        f"email-normal-{servicio_id}",
        _admin(db_cu31),
    )

    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert item.estado == "FALLIDO"
    assert item.destinatario is None
    assert item.ultimo_error == "DESTINATARIO_NO_DISPONIBLE"


@pytest.mark.parametrize(
    ("variable_produccion", "valor_produccion", "correo"),
    [
        ("RENDER", "true", "haroldresend@gmail.com"),
        ("RENDER_SERVICE_ID", "srv-cu31", "haroldresend@gmail.com"),
        ("RENDER_EXTERNAL_HOSTNAME", "api.example.test", "haroldresend@gmail.com"),
        ("APP_ENV", "production", "haroldresend@gmail.com"),
        (None, None, "correo-invalido"),
    ],
)
def test_configuracion_insegura_del_modo_pruebas_es_rechazada(
    monkeypatch, variable_produccion, valor_produccion, correo,
):
    monkeypatch.setattr(config, "EMAIL_TEST", correo)
    for nombre in (
        "RENDER", "RENDER_SERVICE_ID", "RENDER_EXTERNAL_HOSTNAME",
        "APP_ENV", "ENVIRONMENT", "ENV",
    ):
        monkeypatch.delenv(nombre, raising=False)
    if variable_produccion is not None:
        monkeypatch.setenv(variable_produccion, valor_produccion)

    with pytest.raises(RuntimeError):
        config.obtener_configuracion_resend_pruebas()


def test_email_test_ausente_desactiva_override_sin_afectar_produccion(
    monkeypatch,
):
    monkeypatch.setattr(config, "EMAIL_TEST", "")
    monkeypatch.setenv("RENDER", "true")

    assert config.obtener_configuracion_resend_pruebas() is None


def test_error_resend_no_revierte_pago_y_programa_reintento(db_cu31):
    _servicio(db_cu31, 2002)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2002]), "email-2002", _admin(db_cu31),
    )
    class Falla:
        def enviar_comprobante(self, **_kwargs):
            raise ErrorResend("secreto no expuesto")
    assert notificaciones.procesar_siguiente(db_cu31, Falla()) is True
    db_cu31.expire_all()
    assert db_cu31.get(Pago, pago.id).estado == "APROBADO"
    item = db_cu31.scalar(select(NotificacionPago).where(NotificacionPago.pago_id == pago.id))
    assert item.estado == "FALLIDO"
    assert item.proximo_intento is not None
    assert item.ultimo_error == "ENVIO_NO_ACEPTADO"


def test_reintento_automatico_conserva_clave_destinatario_y_payload(db_cu31):
    _servicio(db_cu31, 2008)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2008]), "email-stable-2008", _admin(db_cu31),
    )

    class FallaLuegoAcepta:
        def __init__(self):
            self.llamadas = []

        def enviar_comprobante(self, **kwargs):
            self.llamadas.append(kwargs)
            if len(self.llamadas) == 1:
                raise ErrorResend("rechazo confirmado")
            return RespuestaResend(id="email_retry_ok")

    proveedor = FallaLuegoAcepta()
    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    item.proximo_intento = notificaciones._ahora() - timedelta(seconds=1)
    db_cu31.commit()
    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True

    assert proveedor.llamadas[0] == proveedor.llamadas[1]
    db_cu31.refresh(item)
    assert item.estado == "ACEPTADO_POR_PROVEEDOR"
    assert item.proveedor_mensaje_id == "email_retry_ok"


def test_resultado_desconocido_permite_un_reintento_automatico_idempotente(
    db_cu31,
):
    _servicio(db_cu31, 2009)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2009]), "email-unknown-2009", _admin(db_cu31),
    )

    class RespuestaPerdidaLuegoRecuperada:
        def __init__(self):
            self.llamadas = []

        def enviar_comprobante(self, **kwargs):
            self.llamadas.append(kwargs)
            if len(self.llamadas) == 1:
                raise ResultadoResendDesconocido("respuesta perdida")
            return RespuestaResend(id="email_aceptado_previamente")

    proveedor = RespuestaPerdidaLuegoRecuperada()
    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert item.estado == "FALLIDO"
    assert item.ultimo_error == "RESULTADO_ENVIO_DESCONOCIDO"
    with pytest.raises(HTTPException) as exc:
        administrativos.reintentar_notificacion(
            db_cu31, pago.id, _admin(db_cu31),
        )
    assert exc.value.status_code == 409

    item.proximo_intento = notificaciones._ahora() - timedelta(seconds=1)
    db_cu31.commit()
    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True
    assert proveedor.llamadas[0] == proveedor.llamadas[1]
    db_cu31.refresh(item)
    assert item.estado == "ACEPTADO_POR_PROVEEDOR"
    assert item.proveedor_mensaje_id == "email_aceptado_previamente"


def test_segunda_respuesta_desconocida_queda_en_cuarentena(db_cu31):
    _servicio(db_cu31, 2010)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2010]), "email-unknown-twice-2010", _admin(db_cu31),
    )

    class SiempreIncierto:
        def enviar_comprobante(self, **_kwargs):
            raise ResultadoResendDesconocido("respuesta perdida")

    proveedor = SiempreIncierto()
    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    item.proximo_intento = notificaciones._ahora() - timedelta(seconds=1)
    db_cu31.commit()
    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is True
    item.proximo_intento = notificaciones._ahora() - timedelta(seconds=1)
    db_cu31.commit()

    assert notificaciones.procesar_siguiente(db_cu31, proveedor) is False
    db_cu31.refresh(item)
    assert item.intentos == 2
    assert item.ultimo_error == "RESULTADO_ENVIO_DESCONOCIDO"


def test_correo_invalido_no_intenta_envio(db_cu31, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_TEST", "")
    db_cu31.execute(text("UPDATE usuario SET correo='no-es-correo' WHERE id=11"))
    _servicio(db_cu31, 2003)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2003]), "email-2003", _admin(db_cu31),
    )
    item = db_cu31.scalar(select(NotificacionPago).where(NotificacionPago.pago_id == pago.id))
    assert item.estado == "FALLIDO"
    assert item.ultimo_error == "DESTINATARIO_NO_DISPONIBLE"
    assert notificaciones.procesar_siguiente(db_cu31, _ResendExitoso()) is False


def test_reintento_controlado_reactiva_fallido(db_cu31):
    db_cu31.execute(text("UPDATE usuario SET correo='mal' WHERE id=11"))
    _servicio(db_cu31, 2005)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2005]), "email-2005", _admin(db_cu31),
    )
    db_cu31.execute(text("UPDATE usuario SET correo='ana@example.com' WHERE id=11"))
    db_cu31.commit()
    item = administrativos.reintentar_notificacion(db_cu31, pago.id, _admin(db_cu31))
    assert item.estado == "PENDIENTE"
    assert item.ultimo_error is None


def test_reintento_manual_rechaza_notificacion_en_procesamiento(db_cu31):
    _servicio(db_cu31, 2007)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2007]), "email-processing-2007", _admin(db_cu31),
    )
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    item.estado = "PROCESANDO"
    item.intentos = 1
    item.bloqueado_hasta = notificaciones._ahora() + timedelta(minutes=10)
    db_cu31.commit()

    with pytest.raises(HTTPException) as exc:
        administrativos.reintentar_notificacion(
            db_cu31, pago.id, _admin(db_cu31),
        )

    assert exc.value.status_code == 409
    db_cu31.refresh(item)
    assert item.estado == "PROCESANDO"
    assert item.intentos == 1


def test_worker_recupera_lease_abandonado_dentro_de_ventana_idempotente(
    db_cu31,
):
    _servicio(db_cu31, 2022)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2022]), "email-abandoned-2022", _admin(db_cu31),
    )
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    item.estado = "PROCESANDO"
    item.intentos = 1
    item.fecha_actualizacion = notificaciones._ahora() - timedelta(minutes=11)
    item.bloqueado_hasta = notificaciones._ahora() - timedelta(minutes=1)
    db_cu31.commit()

    assert notificaciones.procesar_siguiente(
        db_cu31, _ResendExitoso(),
    ) is True
    db_cu31.refresh(item)
    assert item.estado == "ACEPTADO_POR_PROVEEDOR"
    assert item.intentos == 2
    assert item.bloqueado_hasta is None


def test_reintento_fallido_recupera_destinatario_de_pruebas(
    db_cu31, monkeypatch,
):
    monkeypatch.setattr(config, "EMAIL_TEST", "")
    db_cu31.execute(text("UPDATE usuario SET correo='' WHERE id=11"))
    db_cu31.commit()
    _servicio(db_cu31, 2006)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2006]), "email-2006", _admin(db_cu31),
    )
    fallida = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert fallida.estado == "FALLIDO"

    _activar_destinatario_pruebas(monkeypatch)
    _contexto_tenant(db_cu31)
    reactivada = administrativos.reintentar_notificacion(
        db_cu31, pago.id, _admin(db_cu31),
    )

    assert reactivada.estado == "PENDIENTE"
    assert reactivada.ultimo_error is None
    persistida = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert persistida.destinatario == "haroldresend@gmail.com"


def test_contrato_oculta_marca_legacy_sin_modificar_registro(
    db_cu31, monkeypatch,
):
    monkeypatch.setattr(config, "EMAIL_TEST", "")
    _servicio(db_cu31, 2007)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2007]), "email-legacy-marker-2007", _admin(db_cu31),
    )
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    item.estado = "ACEPTADO_POR_PROVEEDOR"
    item.ultimo_error = "DESTINATARIO_REDIRECT_PRUEBAS"
    db_cu31.commit()

    respuesta = administrativos.estado_notificacion_administrativa(
        db_cu31, pago.id, _admin(db_cu31),
    )

    assert respuesta.estado == "ACEPTADO_POR_PROVEEDOR"
    assert respuesta.ultimo_error is None
    db_cu31.refresh(item)
    assert item.ultimo_error == "DESTINATARIO_REDIRECT_PRUEBAS"


def test_webhook_resend_firmado_entregado_e_idempotente(db_cu31, monkeypatch):
    _servicio(db_cu31, 2004)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2004]), "email-2004", _admin(db_cu31),
    )
    proveedor = _ResendExitoso()
    notificaciones.procesar_siguiente(db_cu31, proveedor)
    evento = {"type": "email.delivered", "data": {
        "email_id": "email_123", "tags": proveedor.llamadas[0]["tags"],
    }}
    payload = json.dumps(evento, separators=(",", ":")).encode()
    secreto = base64.b64encode(b"webhook-secret-32-bytes-value!!").decode()
    monkeypatch.setattr(config, "RESEND_WEBHOOK_SECRET", f"whsec_{secreto}")
    timestamp = str(int(time.time()))
    firma = base64.b64encode(hmac.new(
        base64.b64decode(secreto), f"msg_1.{timestamp}.".encode() + payload,
        hashlib.sha256,
    ).digest()).decode()
    verificado = notificaciones.verificar_webhook(payload, "msg_1", timestamp, f"v1,{firma}")
    primero = notificaciones.procesar_evento_webhook(db_cu31, verificado)
    segundo = notificaciones.procesar_evento_webhook(db_cu31, verificado)
    assert primero.estado == segundo.estado == "ENTREGADO"
    assert db_cu31.scalar(select(func.count()).select_from(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    )) == 1


def test_webhook_resend_duplicado_y_fuera_de_orden_no_degrada_entregado(
    db_cu31,
):
    _servicio(db_cu31, 2006)
    pago = administrativos.registrar_pago(
        db_cu31, _datos([2006]), "email-events-2006", _admin(db_cu31),
    )
    notificaciones.procesar_siguiente(db_cu31, _ResendExitoso())
    fallido = {"type": "email.failed", "data": {"email_id": "email_123"}}
    entregado = {"type": "email.delivered", "data": {"email_id": "email_123"}}
    rebotado = {"type": "email.bounced", "data": {"email_id": "email_123"}}

    assert notificaciones.procesar_evento_webhook(db_cu31, fallido).estado == "FALLIDO"
    assert notificaciones.procesar_evento_webhook(db_cu31, entregado).estado == "ENTREGADO"
    assert notificaciones.procesar_evento_webhook(db_cu31, entregado).estado == "ENTREGADO"
    assert notificaciones.procesar_evento_webhook(db_cu31, rebotado).estado == "ENTREGADO"
    item = db_cu31.scalar(select(NotificacionPago).where(
        NotificacionPago.pago_id == pago.id,
    ))
    assert item.ultimo_error is None


def test_webhook_resend_rechaza_firma_invalida(monkeypatch):
    secreto = base64.b64encode(b"webhook-secret-32-bytes-value!!").decode()
    monkeypatch.setattr(config, "RESEND_WEBHOOK_SECRET", f"whsec_{secreto}")
    with pytest.raises(HTTPException) as exc:
        notificaciones.verificar_webhook(
            b"{}", "msg_1", str(int(time.time())), "v1,firma-invalida",
        )
    assert exc.value.status_code == 400


def test_provider_resend_envia_pdf_base64_e_idempotency(monkeypatch):
    class Respuesta:
        def raise_for_status(self):
            return None
        def json(self):
            return {"id": "email_provider"}
    class Cliente:
        def __init__(self):
            self.peticion = None
        def post(self, *args, **kwargs):
            self.peticion = (args, kwargs)
            return Respuesta()
    cliente = Cliente()
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test_secret")
    monkeypatch.setattr(config, "RESEND_FROM_EMAIL", "onboarding@resend.dev")
    resultado = ResendProvider(cliente).enviar_comprobante(
        destinatario="ana@example.com", pago_id=7, pdf=b"%PDF-demo",
        nombre_archivo="comprobante.pdf", clave_idempotencia="tenant/7",
        tags={"pago_id": "7"},
    )
    assert resultado.id == "email_provider"
    _, kwargs = cliente.peticion
    assert kwargs["headers"]["Idempotency-Key"] == "tenant/7"
    assert base64.b64decode(kwargs["json"]["attachments"][0]["content"]) == b"%PDF-demo"


def test_provider_resend_clasifica_timeout_como_resultado_desconocido(monkeypatch):
    class Cliente:
        def post(self, *args, **kwargs):
            request = httpx.Request("POST", args[0])
            raise httpx.ReadTimeout("respuesta perdida", request=request)

    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test_secret")
    monkeypatch.setattr(config, "RESEND_FROM_EMAIL", "onboarding@resend.dev")

    with pytest.raises(ResultadoResendDesconocido):
        ResendProvider(Cliente()).enviar_comprobante(
            destinatario="ana@example.com",
            pago_id=7,
            pdf=b"%PDF-demo",
            nombre_archivo="comprobante.pdf",
            clave_idempotencia="tenant/7",
            tags={"pago_id": "7"},
        )


def test_variables_resend_ausentes_no_rompen_import_y_fallan_controlado(monkeypatch):
    from app.modules.gestion_pagos.providers.resend_provider import ResendNoConfigurado, ResendProvider
    monkeypatch.setattr(config, "RESEND_API_KEY", None)
    monkeypatch.setattr(config, "RESEND_FROM_EMAIL", None)
    with pytest.raises(ResendNoConfigurado):
        ResendProvider().enviar_comprobante(
            destinatario="a@example.com", pago_id=1, pdf=b"%PDF", nombre_archivo="x.pdf",
            clave_idempotencia="k", tags={},
        )
