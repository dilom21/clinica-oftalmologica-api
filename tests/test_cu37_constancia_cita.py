# -*- coding: utf-8 -*-
"""CU37 - Emitir constancia de cita médica (multitenant)."""
from datetime import date, datetime, time, timezone
from io import BytesIO
from types import SimpleNamespace

from pypdf import PdfReader
from sqlalchemy import text

from app.modules.gestion_agenda_citas.services.constancia_pdf import (
    DatosConstancia,
    generar_constancia_pdf,
)
from tests.test_cu10_paciente_movil import (  # noqa: F401  (fixture + helpers)
    AHORA,
    DIA1,
    _cabecera,
    _token_paciente,
    _token_recepcionista,
    cliente_citas,
)


def _insertar_cita(db, cita_id, paciente_id, estado):
    db.execute(
        text(
            "INSERT INTO cita VALUES "
            "(:id,:pac,1,:fecha,'11:00:00','11:30:00','Control anual',NULL,"
            ":estado,'WEB',2,:ahora,:ahora)"
        ),
        {
            "id": cita_id,
            "pac": paciente_id,
            "fecha": DIA1,
            "estado": estado,
            "ahora": AHORA,
        },
    )
    db.commit()


def _contexto_tenant(nombre, codigo, empresa_id):
    return SimpleNamespace(
        empresa_nombre=nombre,
        empresa_codigo=codigo,
        empresa_id=empresa_id,
        tenant_database_id=empresa_id,
        database_name=f"db_{codigo}",
    )


def _texto_pdf(contenido: bytes) -> str:
    lector = PdfReader(BytesIO(contenido))
    return " ".join(pagina.extract_text() for pagina in lector.pages)


def test_personal_descarga_constancia_de_cita_atendida(cliente_citas):
    client, db, _ = cliente_citas
    _insertar_cita(db, 2, 8, "ATENDIDA")
    db.info["tenant_context"] = _contexto_tenant("Clinica Vista Sana", "vs", 1)

    r = client.get(
        "/agenda-citas/citas/2/constancia",
        headers=_cabecera(_token_recepcionista()),
    )

    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert "constancia-cita-CC-000002.pdf" in r.headers["content-disposition"]
    texto = _texto_pdf(r.content)
    assert "CLINICA VISTA SANA" in texto
    assert "CC-000002" in texto
    assert "Ana" in texto and "Fernández" in texto
    assert "MAT-1" in texto
    assert "11:00" in texto and "11:30" in texto


def test_constancia_usa_la_clinica_de_cada_tenant(cliente_citas):
    client, db, _ = cliente_citas
    _insertar_cita(db, 2, 8, "ATENDIDA")
    cabecera = _cabecera(_token_recepcionista())

    db.info["tenant_context"] = _contexto_tenant("Clinica Uno", "c1", 1)
    uno = client.get("/agenda-citas/citas/2/constancia", headers=cabecera)
    db.info["tenant_context"] = _contexto_tenant("Oftalmos Dos", "c2", 2)
    dos = client.get("/agenda-citas/citas/2/constancia", headers=cabecera)

    assert uno.status_code == dos.status_code == 200
    assert "CLINICA UNO" in _texto_pdf(uno.content)
    assert "OFTALMOS DOS" in _texto_pdf(dos.content)
    assert "OFTALMOS DOS" not in _texto_pdf(uno.content)


def test_cita_no_atendida_no_emite_constancia(cliente_citas):
    client, db, _ = cliente_citas
    _insertar_cita(db, 3, 8, "PROGRAMADA")

    r = client.get(
        "/agenda-citas/citas/3/constancia",
        headers=_cabecera(_token_recepcionista()),
    )

    assert r.status_code == 409
    assert "ATENDIDA" in r.json()["detail"]


def test_cita_inexistente_devuelve_404(cliente_citas):
    client, _, _ = cliente_citas

    r = client.get(
        "/agenda-citas/citas/999/constancia",
        headers=_cabecera(_token_recepcionista()),
    )

    assert r.status_code == 404


def test_paciente_solo_descarga_constancia_de_sus_citas(cliente_citas):
    client, db, _ = cliente_citas
    _insertar_cita(db, 2, 8, "ATENDIDA")   # del paciente autenticado (id 8)
    _insertar_cita(db, 4, 9, "ATENDIDA")   # de otro paciente
    cabecera = _cabecera(_token_paciente())

    propia = client.get("/agenda-citas/citas/2/constancia", headers=cabecera)
    ajena = client.get("/agenda-citas/citas/4/constancia", headers=cabecera)

    assert propia.status_code == 200
    assert ajena.status_code == 404


def test_emitir_constancia_queda_en_bitacora(cliente_citas):
    client, db, _ = cliente_citas
    _insertar_cita(db, 2, 8, "ATENDIDA")

    r = client.get(
        "/agenda-citas/citas/2/constancia",
        headers=_cabecera(_token_recepcionista()),
    )

    assert r.status_code == 200
    filas = db.execute(
        text(
            "SELECT usuario_id, entidad_afectada, id_registro_afectado "
            "FROM bitacora WHERE accion = 'EMITIR_CONSTANCIA_CITA'"
        )
    ).all()
    assert filas == [(2, "cita", 2)]


def test_sin_token_no_emite_constancia(cliente_citas):
    client, db, _ = cliente_citas
    _insertar_cita(db, 2, 8, "ATENDIDA")

    r = client.get("/agenda-citas/citas/2/constancia")

    assert r.status_code in (401, 403)


def test_pdf_escapa_texto_del_paciente():
    datos = DatosConstancia(
        clinica_nombre="Clinica <b>X</b> & Co",
        cita_id=7,
        fecha_emision=datetime.now(timezone.utc),
        paciente_nombres="Ana <i>",
        paciente_apellidos="O'Neil & Pérez",
        paciente_ci=None,
        oftalmologo_nombres="Salet",
        oftalmologo_apellidos="Ejemplo",
        oftalmologo_matricula=None,
        oftalmologo_especialidad=None,
        fecha_cita=date(2026, 11, 20),
        hora_inicio=time(9, 0),
        hora_fin=time(9, 30),
        motivo=None,
        estado="ATENDIDA",
    )

    contenido = generar_constancia_pdf(datos)

    assert contenido.startswith(b"%PDF")
    assert "O'Neil & Pérez" in _texto_pdf(contenido)
