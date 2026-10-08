from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import ACCION_LECTURA, get_db, obtener_administrador_actual, requerir_permiso
from app.modules.gestion_reportes.registry.datasets import DATASETS, STATIC_REPORTS
from app.modules.gestion_reportes.schemas.schemas import ReporteDinamico, ReporteDinamicoEmail, ReporteEstatico, ReporteEstaticoEmail
from app.modules.gestion_reportes.services.service import run
from app.modules.gestion_reportes.services.exporters.csv_exporter import export_csv
from app.modules.gestion_reportes.services.exporters.excel_exporter import export_xlsx
from app.modules.gestion_reportes.services.exporters.pdf_exporter import export_pdf
from app.modules.gestion_reportes.services.exporters.html_exporter import export_html
from app.shared.services import email_service
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora

router = APIRouter(prefix="/reportes", tags=["Reportes"], dependencies=[Depends(obtener_administrador_actual), Depends(requerir_permiso("Generar reportes", ACCION_LECTURA))])


@router.get("/catalogo")
def catalogo():
    return {"datasets": [{"key": key, "label": value["label"], "campos": [{"key": name, "label": field.label, "tipo": field.tipo, "operadores": list(field.operadores)} for name, field in value["fields"].items()]} for key, value in DATASETS.items()], "reportes_estaticos": [{"key": key, "label": value[2], "dataset": value[0], "columnas": list(value[1])} for key, value in STATIC_REPORTS.items()]}


def _preview(db, dataset, columns, filters, orders, limit):
    metadata, rows, total = run(db, dataset, columns, filters, orders, limit)
    return {"columnas": metadata, "filas": rows, "total": total, "limit": limit}


def _static_filters(reporte_key, filters):
    filters = list(filters)
    if reporte_key == "pacientes_activos":
        from app.modules.gestion_reportes.schemas.schemas import FiltroReporte
        filters.append(FiltroReporte(campo="estado", operador="eq", valor=True))
    return filters


@router.post("/dinamicos/previsualizar")
def preview_dynamic(payload: ReporteDinamico, db: Session = Depends(get_db)):
    return _preview(db, payload.dataset, payload.columnas, payload.filtros, payload.orden, payload.limit)


EXPORTERS = {
    "xlsx": (export_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pdf": (export_pdf, "application/pdf", "application", "pdf"),
    "csv": (export_csv, "text/csv; charset=utf-8", "text", "csv"),
    "html": (export_html, "text/html; charset=utf-8", "text", "html"),
}


def _build_export(db, dataset, columns, filters, orders, title, fmt):
    if fmt not in EXPORTERS:
        raise HTTPException(422, "Formato inválido")
    metadata, rows, total = run(db, dataset, columns, filters, orders, 50, export=True)
    body = EXPORTERS[fmt][0](title, metadata, rows)
    return body, total


def _record_audit(request, db, user, action, description):
    try:
        registrar_bitacora(db, user.id, action, ip=request.client.host if request.client else None, entidad_afectada="reporte", descripcion=description)
        db.commit()
    except Exception:
        db.rollback()
        raise
def _export(request, db, user, dataset, columns, filters, orders, title, fmt):
    body, _ = _build_export(db, dataset, columns, filters, orders, title, fmt)
    _record_audit(request, db, user, f"EXPORTAR_REPORTE_{fmt.upper()}", f"Exportación de {title}")
    return Response(body, media_type=EXPORTERS[fmt][1], headers={"Content-Disposition": f'attachment; filename="{dataset}.{fmt}"'})


def _send_email(request, db, user, dataset, columns, filters, orders, title, payload):
    body, _ = _build_export(db, dataset, columns, filters, orders, title, payload.formato)
    try:
        email_service.enviar_reporte_por_smtp(
            str(payload.destinatario), payload.asunto, payload.mensaje, body,
            f"{dataset}.{payload.formato}", EXPORTERS[payload.formato][2], EXPORTERS[payload.formato][3],
        )
    except email_service.SMTPNoConfiguradoError:
        raise HTTPException(503, "Servicio de correo no configurado") from None
    except Exception:
        raise HTTPException(503, "No se pudo enviar el reporte por correo") from None
    _record_audit(request, db, user, "ENVIAR_REPORTE_EMAIL", f"Envío por correo de {title} en formato {payload.formato.upper()}")
    return {"mensaje": "Reporte enviado por correo"}


@router.post("/dinamicos/exportar/{formato}")
def export_dynamic(formato: str, payload: ReporteDinamico, request: Request, db: Session = Depends(get_db), user=Depends(obtener_administrador_actual)):
    return _export(request, db, user, payload.dataset, payload.columnas, payload.filtros, payload.orden, "Reporte dinámico", formato)


@router.post("/dinamicos/enviar-email")
def email_dynamic(payload: ReporteDinamicoEmail, request: Request, db: Session = Depends(get_db), user=Depends(obtener_administrador_actual)):
    return _send_email(request, db, user, payload.dataset, payload.columnas, payload.filtros, payload.orden, "Reporte dinámico", payload)


@router.post("/estaticos/{reporte_key}/previsualizar")
def preview_static(reporte_key: str, payload: ReporteEstatico, db: Session = Depends(get_db)):
    if reporte_key not in STATIC_REPORTS:
        raise HTTPException(404, "Reporte no encontrado")
    dataset, columns, _ = STATIC_REPORTS[reporte_key]
    orders = []
    filters = _static_filters(reporte_key, payload.filtros)
    return _preview(db, dataset, list(columns), filters, orders, payload.limit)


@router.post("/estaticos/{reporte_key}/exportar/{formato}")
def export_static(reporte_key: str, formato: str, payload: ReporteEstatico, request: Request, db: Session = Depends(get_db), user=Depends(obtener_administrador_actual)):
    if reporte_key not in STATIC_REPORTS:
        raise HTTPException(404, "Reporte no encontrado")
    dataset, columns, title = STATIC_REPORTS[reporte_key]
    filters = _static_filters(reporte_key, payload.filtros)
    return _export(request, db, user, dataset, list(columns), filters, [], title, formato)


@router.post("/estaticos/{reporte_key}/enviar-email")
def email_static(reporte_key: str, payload: ReporteEstaticoEmail, request: Request, db: Session = Depends(get_db), user=Depends(obtener_administrador_actual)):
    if reporte_key not in STATIC_REPORTS:
        raise HTTPException(404, "Reporte no encontrado")
    dataset, columns, title = STATIC_REPORTS[reporte_key]
    return _send_email(request, db, user, dataset, list(columns), _static_filters(reporte_key, payload.filtros), [], title, payload)
