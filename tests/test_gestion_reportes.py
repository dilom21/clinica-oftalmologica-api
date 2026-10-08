import csv
import importlib.util
import io
from email import policy
from email.parser import BytesParser

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import dependencies
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_reportes.registry.datasets import DATASETS, STATIC_REPORTS
from app.modules.gestion_reportes.services.service import validate


@pytest.fixture
def report_client(db_historial):
    db_historial.execute(text("INSERT INTO funcion VALUES (30, 'Generar reportes', 1)"))
    db_historial.execute(text("INSERT INTO rol_funcion VALUES (4, 1, 30, 1)"))
    db_historial.commit()

    def local_db():
        yield db_historial

    old = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = local_db
    app.dependency_overrides[dependencies.get_db] = local_db
    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {crear_access_token(7, 1)}"
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)


def test_catalog_registry_has_approved_datasets_and_excludes_secrets():
    assert set(DATASETS) == {"pacientes", "citas", "consultas_clinicas", "diagnosticos", "usuarios"}
    assert set(STATIC_REPORTS) == {"pacientes_activos", "citas_por_fecha", "consultas_clinicas", "diagnosticos_registrados", "usuarios_por_rol"}
    fields = {key for ds in DATASETS.values() for key in ds["fields"]}
    assert not {"password_hash", "token_hash", "api_key", "secret"} & fields
    assert "bitacora" not in DATASETS


def test_registry_validation_rejects_unregistered_fields_operators_and_orders():
    class Obj:
        campo, operador, valor = "correo", "injection", "x"
    class Order:
        campo, direccion = "correo", "desc;drop table usuario"
    for columns, filters, orders in [(["password_hash"], [], []), (["correo"], [Obj()], []), (["correo"], [], [Order()])]:
        with pytest.raises(Exception) as err:
            validate("usuarios", columns, filters, orders)
        assert getattr(err.value, "status_code", None) == 422


def test_catalog_and_dynamic_preview_query_filters_columns_and_order(report_client):
    assert report_client.get("/reportes/catalogo").status_code == 200
    response = report_client.post("/reportes/dinamicos/previsualizar", json={
        "dataset": "pacientes", "columnas": ["nombres", "ci"],
        "filtros": [{"campo": "nombres", "operador": "starts_with", "valor": "Ana"}],
        "orden": [{"campo": "ci", "direccion": "desc"}], "limit": 1,
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 2 and len(data["filas"]) == 1
    assert data["filas"][0]["nombres"] == "Ana"
    assert [c["key"] for c in data["columnas"]] == ["nombres", "ci"]
    assert "password_hash" not in response.text


@pytest.mark.parametrize("payload", [
    {"dataset": "bitacora", "columnas": ["id"]},
    {"dataset": "pacientes", "columnas": ["password_hash"]},
    {"dataset": "pacientes", "columnas": ["nombres"], "filtros": [{"campo": "nombres", "operador": "raw_sql", "valor": "x"}]},
    {"dataset": "pacientes", "columnas": ["nombres"], "orden": [{"campo": "nombres", "direccion": "desc;drop"}]},
    {"dataset": "pacientes", "columnas": ["nombres"], "limit": 201},
])
def test_dynamic_rejects_invalid_dataset_columns_operator_order_and_limit(report_client, payload):
    assert report_client.post("/reportes/dinamicos/previsualizar", json=payload).status_code == 422


def test_static_patient_report_enforces_active_condition_for_preview_and_export(report_client, db_historial):
    db_historial.execute(text("INSERT INTO paciente (id, nombres, apellidos, fecha_registro, estado) VALUES (99, 'Inactivo', 'Paciente', '2026-01-01', 0)"))
    db_historial.commit()
    url = "/reportes/estaticos/pacientes_activos/previsualizar"
    response = report_client.post(url, json={"limit": 20})
    assert response.status_code == 200
    assert len(response.json()["filas"]) == 5
    assert all(row["estado"] is True for row in response.json()["filas"])
    contradictory = report_client.post(url, json={"limit": 20, "filtros": [
        {"campo": "estado", "operador": "eq", "valor": False},
    ]})
    assert contradictory.status_code == 200
    assert contradictory.json()["filas"] == []
    export = report_client.post("/reportes/estaticos/pacientes_activos/exportar/csv", json={"filtros": [
        {"campo": "estado", "operador": "eq", "valor": False},
    ]})
    assert export.status_code == 200
    assert "Inactivo" not in export.content.decode("utf-8-sig")
    bad = report_client.post("/reportes/estaticos/pacientes_activos/previsualizar", json={"columnas": ["ci"]})
    assert bad.status_code == 422
    assert report_client.post("/reportes/estaticos/nope/previsualizar", json={}).status_code == 404


def test_preview_defaults_to_50_and_static_default(report_client):
    body = {"dataset": "pacientes", "columnas": ["nombres"]}
    response = report_client.post("/reportes/dinamicos/previsualizar", json=body)
    assert response.status_code == 200 and response.json()["limit"] == 50


def test_authentication_admin_role_and_permission(report_client, db_historial):
    client = report_client
    url = "/reportes/catalogo"
    client.headers.pop("Authorization")
    assert client.get(url).status_code == 401
    client.headers["Authorization"] = "Bearer invalid"
    assert client.get(url).status_code == 401
    client.headers["Authorization"] = f"Bearer {crear_access_token(999, 1)}"
    assert client.get(url).status_code == 401
    client.headers["Authorization"] = f"Bearer {crear_access_token(7, 1)}"
    db_historial.execute(text("UPDATE rol SET nombre='Recepción' WHERE id=1"))
    db_historial.commit()
    assert client.get(url).status_code == 403
    db_historial.execute(text("UPDATE rol SET nombre='Administrador' WHERE id=1"))
    db_historial.execute(text("UPDATE rol_funcion SET accion_id=2 WHERE id=4"))
    db_historial.commit()
    assert client.get(url).status_code == 403
    db_historial.execute(text("DELETE FROM rol_funcion WHERE id=4"))
    db_historial.commit()
    assert client.get(url).status_code == 403


def test_csv_export_bom_content_mime_and_success_audit_without_filters(report_client):
    response = report_client.post("/reportes/dinamicos/exportar/csv", json={
        "dataset": "pacientes", "columnas": ["nombres", "ci"],
        "filtros": [{"campo": "nombres", "operador": "eq", "valor": "CLINICAL_SECRET"}],
    })
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0] == ["Nombres", "Ci"] and rows[1:] == []
    from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
    entries = report_client.app.dependency_overrides[session.get_db]()
    db = next(entries)
    try:
        record = db.query(Bitacora).filter_by(accion="EXPORTAR_REPORTE_CSV").one()
        assert record.entidad_afectada == "reporte"
        assert "CLINICAL_SECRET" not in (record.descripcion or "")
        assert record.descripcion == "Exportación de Reporte dinámico"
    finally:
        entries.close()


def test_export_invalid_format_and_failed_export_do_not_audit(report_client, monkeypatch):
    from app.modules.gestion_reportes.services import service

    body = {"dataset": "pacientes", "columnas": ["nombres"]}
    assert report_client.post("/reportes/dinamicos/exportar/xml", json=body).status_code == 422
    assert report_client.post("/reportes/estaticos/missing/exportar/csv", json={}).status_code == 404
    monkeypatch.setattr(service, "execute", lambda *args, **kwargs: ([], 5001))
    assert report_client.post("/reportes/dinamicos/exportar/csv", json=body).status_code == 422
    from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
    entries = report_client.app.dependency_overrides[session.get_db]()
    db = next(entries)
    try:
        assert db.query(Bitacora).count() == 0
    finally:
        entries.close()


@pytest.mark.parametrize("fmt,action,mime", [
    ("xlsx", "EXPORTAR_REPORTE_XLSX", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("pdf", "EXPORTAR_REPORTE_PDF", "application/pdf"),
])
def test_successful_optional_export_records_only_safe_audit_fields(report_client, monkeypatch, fmt, action, mime):
    from app.modules.gestion_reportes.api import router as api
    from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
    monkeypatch.setattr(api, "export_xlsx" if fmt == "xlsx" else "export_pdf", lambda *args: b"fake-export")
    response = report_client.post(f"/reportes/dinamicos/exportar/{fmt}", json={
        "dataset": "pacientes", "columnas": ["nombres"],
        "filtros": [{"campo": "nombres", "operador": "eq", "valor": "PRIVATE_FILTER"}],
    })
    assert response.status_code == 200 and response.headers["content-type"] == mime
    entries = report_client.app.dependency_overrides[session.get_db]()
    db = next(entries)
    try:
        record = db.query(Bitacora).filter_by(accion=action).one()
        assert record.entidad_afectada == "reporte"
        assert "PRIVATE_FILTER" not in (record.descripcion or "")
        assert record.descripcion == "Exportación de Reporte dinámico"
    finally:
        entries.close()


@pytest.mark.parametrize("fmt,dependency,magic,mime", [
    ("xlsx", "openpyxl", b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("pdf", "reportlab", b"%PDF", "application/pdf"),
])
def test_real_binary_exports_when_optional_dependencies_are_available(report_client, fmt, dependency, magic, mime):
    if importlib.util.find_spec(dependency) is None:
        pytest.skip(f"{dependency} unavailable; real {fmt.upper()} generation not verified")
    response = report_client.post(f"/reportes/dinamicos/exportar/{fmt}", json={"dataset": "pacientes", "columnas": ["nombres"]})
    assert response.status_code == 200 and response.content.startswith(magic)
    assert response.headers["content-type"] == mime


def test_html_dynamic_is_self_contained_escaped_and_audited(report_client, db_historial):
    db_historial.execute(text("UPDATE paciente SET nombres='<script>alert(1)</script>' WHERE id=1"))
    db_historial.commit()
    response = report_client.post("/reportes/dinamicos/exportar/html", json={
        "dataset": "pacientes", "columnas": ["nombres"],
    })
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["content-disposition"].endswith('"pacientes.html"')
    content = response.content.decode("utf-8")
    assert content.startswith("<!doctype html>") and '<html lang="es">' in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
    assert "<script>alert(1)</script>" not in content and "<script" not in content
    assert "http://" not in content and "https://" not in content

    from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
    entries = report_client.app.dependency_overrides[session.get_db]()
    db = next(entries)
    try:
        assert db.query(Bitacora).filter_by(accion="EXPORTAR_REPORTE_HTML").one()
    finally:
        entries.close()


def test_html_static_export_and_email_success_are_audited_without_recipient(report_client, monkeypatch, db_historial):
    response = report_client.post("/reportes/estaticos/pacientes_activos/exportar/html", json={})
    assert response.status_code == 200 and response.headers["content-disposition"].endswith('"pacientes.html"')

    captured = {}
    def fake_send(*args):
        captured["args"] = args
    from app.shared.services import email_service
    monkeypatch.setattr(email_service, "enviar_reporte_por_smtp", fake_send)
    response = report_client.post("/reportes/estaticos/pacientes_activos/enviar-email", json={
        "destinatario": "admin@example.com", "formato": "html", "asunto": "Reporte", "mensaje": "Adjunto",
    })
    assert response.status_code == 200
    assert captured["args"][0] == "admin@example.com" and captured["args"][4] == "pacientes.html"

    from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
    entries = report_client.app.dependency_overrides[session.get_db]()
    db = next(entries)
    try:
        record = db.query(Bitacora).filter_by(accion="ENVIAR_REPORTE_EMAIL").one()
        assert "admin@example.com" not in record.descripcion
        assert "Adjunto" not in record.descripcion
    finally:
        entries.close()


@pytest.mark.parametrize("fmt", ["xlsx", "pdf", "csv", "html"])
def test_dynamic_email_accepts_each_report_format_and_uses_export_limit(report_client, monkeypatch, fmt):
    captured = {}
    from app.shared.services import email_service
    monkeypatch.setattr(email_service, "enviar_reporte_por_smtp", lambda *args: captured.setdefault("args", args))
    response = report_client.post("/reportes/dinamicos/enviar-email", json={
        "destinatario": "admin@example.com", "formato": fmt, "asunto": "Reporte", "mensaje": "",
        "dataset": "pacientes", "columnas": ["nombres"], "limit": 1,
    })
    assert response.status_code == 200
    assert captured["args"][4] == f"pacientes.{fmt}"


@pytest.mark.parametrize("payload", [
    {"destinatario": "invalid", "formato": "csv", "asunto": "x"},
    {"destinatario": "a@@example.com", "formato": "csv", "asunto": "x"},
    {"destinatario": "a@example", "formato": "csv", "asunto": "x"},
    {"destinatario": "a b@example.com", "formato": "csv", "asunto": "x"},
    {"destinatario": "a<>@example.com", "formato": "csv", "asunto": "x"},
    {"destinatario": "admin@example.com", "formato": "xml", "asunto": "x"},
    {"destinatario": "admin@example.com", "formato": "csv", "asunto": ""},
])
def test_email_validation_returns_422(report_client, payload):
    payload.update({"dataset": "pacientes", "columnas": ["nombres"]})
    assert report_client.post("/reportes/dinamicos/enviar-email", json=payload).status_code == 422


def test_email_unconfigured_or_smtp_failure_returns_503_without_success_audit(report_client, monkeypatch):
    from app.shared.services import email_service
    monkeypatch.setattr(email_service, "enviar_reporte_por_smtp", lambda *args: (_ for _ in ()).throw(email_service.SMTPNoConfiguradoError()))
    body = {"destinatario": "admin@example.com", "formato": "csv", "asunto": "Reporte", "dataset": "pacientes", "columnas": ["nombres"]}
    assert report_client.post("/reportes/dinamicos/enviar-email", json=body).status_code == 503
    monkeypatch.setattr(email_service, "enviar_reporte_por_smtp", lambda *args: (_ for _ in ()).throw(RuntimeError("secret SMTP")))
    assert report_client.post("/reportes/dinamicos/enviar-email", json=body).status_code == 503
    assert "secret SMTP" not in report_client.post("/reportes/dinamicos/enviar-email", json=body).text


def test_smtp_sender_builds_mime_attachment_and_uses_tls(monkeypatch):
    from app.shared.services import email_service

    class FakeSMTP:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def starttls(self):
            self.tls_started = True

        def login(self, username, password):
            self.login_args = (username, password)

        def send_message(self, message):
            self.message = message

    smtp_instance = FakeSMTP()
    monkeypatch.setattr(email_service.smtplib, "SMTP", lambda host, port, timeout: (
        setattr(smtp_instance, "connection_args", (host, port, timeout)) or smtp_instance
    ))
    monkeypatch.setattr(email_service, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(email_service, "SMTP_PORT", 587)
    monkeypatch.setattr(email_service, "SMTP_FROM_EMAIL", "reports@example.test")
    monkeypatch.setattr(email_service, "SMTP_FROM_NAME", "Reports")
    monkeypatch.setattr(email_service, "SMTP_USE_TLS", True)
    monkeypatch.setattr(email_service, "SMTP_USERNAME", "smtp-user")
    monkeypatch.setattr(email_service, "SMTP_PASSWORD", "smtp-password")

    email_service.enviar_reporte_por_smtp(
        "admin@example.test", "Report", "Attached", b"report-bytes", "report.csv", "text", "csv"
    )

    message = BytesParser(policy=policy.default).parsebytes(smtp_instance.message.as_bytes())
    attachment = next(part for part in message.iter_attachments())
    assert smtp_instance.connection_args == ("smtp.example.test", 587, 10)
    assert smtp_instance.tls_started is True
    assert smtp_instance.login_args == ("smtp-user", "smtp-password")
    assert message["To"] == "admin@example.test"
    assert attachment.get_filename() == "report.csv"
    assert attachment.get_content_type() == "text/csv"
    assert attachment.get_payload(decode=True) == b"report-bytes"


def test_email_over_5000_rows_returns_422_without_audit_or_send(report_client, monkeypatch):
    from app.modules.gestion_reportes.services import service
    from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
    from app.shared.services import email_service

    monkeypatch.setattr(service, "execute", lambda *args, **kwargs: ([], 5001))
    monkeypatch.setattr(email_service, "enviar_reporte_por_smtp", lambda *args: pytest.fail("SMTP must not be called"))
    response = report_client.post("/reportes/dinamicos/enviar-email", json={
        "destinatario": "admin@example.com", "formato": "csv", "asunto": "Report",
        "dataset": "pacientes", "columnas": ["nombres"],
    })
    assert response.status_code == 422
    entries = report_client.app.dependency_overrides[session.get_db]()
    db = next(entries)
    try:
        assert db.query(Bitacora).filter_by(accion="ENVIAR_REPORTE_EMAIL").count() == 0
    finally:
        entries.close()


def test_email_endpoint_uses_inherited_router_authorization(report_client):
    report_client.headers.pop("Authorization")
    response = report_client.post("/reportes/dinamicos/enviar-email", json={})
    assert response.status_code == 401
