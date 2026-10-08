"""Generacion del comprobante de pago en PDF (ETAPA 8.1).

Este modulo solo construye el documento a partir de datos ya resueltos por
la capa de servicio. No consulta la base de datos ni modifica estado alguno:
la generacion de un comprobante es una operacion de lectura pura.

Se utiliza ReportLab con formato A4. Los importes se manejan con ``Decimal``
y se formatean con dos decimales. Las fechas se convierten a la zona horaria
de Bolivia (America/La_Paz) antes de mostrarse.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ZONA_BOLIVIA = ZoneInfo("America/La_Paz")
LIMITE_ZONA = "Horario de Bolivia (America/La_Paz, UTC-4)"
_FORMATO_FECHA_HORA = "%d/%m/%Y %H:%M"

_AZUL = colors.HexColor("#1A3E6E")
_AZUL_CLARO = colors.HexColor("#2E5A8E")
_GRIS_TEXTO = colors.HexColor("#333333")
_GRIS_SUAVE = colors.HexColor("#6B6B6B")
_GRIS_LINEA = colors.HexColor("#C9D3E0")
_FONDO_CABECERA = colors.HexColor("#EAF0F7")


@dataclass(frozen=True)
class ServicioComprobante:
    """Linea del detalle de servicios del comprobante."""

    nombre: str
    monto: Decimal


@dataclass(frozen=True)
class DatosComprobante:
    """Datos planos necesarios para construir el comprobante."""

    pago_id: int
    consulta_clinica_id: int | None
    consulta_fecha: datetime | None
    fecha_pago: datetime | None
    fecha_emision: datetime
    estado: str
    metodo_pago: str
    pasarela: str | None
    moneda: str
    referencia_transaccion: str | None
    paciente_nombres: str
    paciente_apellidos: str
    monto_total: Decimal
    servicios: list[ServicioComprobante] = field(default_factory=list)


def _decimal(valor) -> Decimal:
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def a_zona_bolivia(valor: datetime | None) -> datetime | None:
    """Convierte un datetime (o fecha naive asumida UTC) a America/La_Paz."""
    if valor is None:
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(ZONA_BOLIVIA)


def _formatear_fecha_hora(valor: datetime | None) -> str:
    convertido = a_zona_bolivia(valor)
    if convertido is None:
        return "No registrada"
    return convertido.strftime(_FORMATO_FECHA_HORA)


def _formatear_monto(monto, moneda: str) -> str:
    monto = _decimal(monto).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    codigo = (moneda or "BOB").upper()
    if codigo == "BOB":
        return f"Bs {monto:.2f}"
    return f"{monto:.2f} {codigo}"


def _crear_estilos() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle(
            "titulo", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=19, leading=23, alignment=TA_CENTER, textColor=_AZUL,
        ),
        "subtitulo": ParagraphStyle(
            "subtitulo", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=12.5, leading=16, alignment=TA_CENTER, textColor=_AZUL_CLARO,
        ),
        "identificador": ParagraphStyle(
            "identificador", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=12, alignment=TA_CENTER, textColor=_GRIS_SUAVE,
        ),
        "seccion": ParagraphStyle(
            "seccion", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=10.5, leading=13, spaceBefore=6, spaceAfter=3, textColor=_AZUL,
        ),
        "etiqueta": ParagraphStyle(
            "etiqueta", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9, leading=12, textColor=_GRIS_SUAVE,
        ),
        "valor": ParagraphStyle(
            "valor", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=12, textColor=_GRIS_TEXTO,
        ),
        "celda": ParagraphStyle(
            "celda", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=12, textColor=_GRIS_TEXTO,
        ),
        "celda_derecha": ParagraphStyle(
            "celda_derecha", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=12, alignment=TA_RIGHT, textColor=_GRIS_TEXTO,
        ),
        "celda_total": ParagraphStyle(
            "celda_total", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=10, leading=13, alignment=TA_RIGHT, textColor=_AZUL,
        ),
        "cabecera": ParagraphStyle(
            "cabecera", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9.5, leading=12, textColor=colors.white,
        ),
        "cabecera_derecha": ParagraphStyle(
            "cabecera_derecha", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9.5, leading=12, alignment=TA_RIGHT, textColor=colors.white,
        ),
        "nota": ParagraphStyle(
            "nota", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=8.5, leading=11, textColor=_GRIS_SUAVE, spaceBefore=8,
        ),
    }


def _tabla_informacion(
    filas: list[tuple[str, str, str, str]],
    estilos: dict[str, ParagraphStyle],
    ancho_total: float,
) -> Table:
    datos = [
        [
            Paragraph(etiqueta_1, estilos["etiqueta"]),
            Paragraph(valor_1, estilos["valor"]),
            Paragraph(etiqueta_2, estilos["etiqueta"]),
            Paragraph(valor_2, estilos["valor"]),
        ]
        for etiqueta_1, valor_1, etiqueta_2, valor_2 in filas
    ]
    tabla = Table(
        datos,
        colWidths=[
            ancho_total * 0.22, ancho_total * 0.30,
            ancho_total * 0.22, ancho_total * 0.26,
        ],
        hAlign="LEFT",
    )
    tabla.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, _GRIS_LINEA),
    ]))
    return tabla


def _tabla_servicios(
    datos: DatosComprobante,
    estilos: dict[str, ParagraphStyle],
    ancho_total: float,
) -> Table:
    filas = [[
        Paragraph("#", estilos["cabecera"]),
        Paragraph("Servicio", estilos["cabecera"]),
        Paragraph("Importe", estilos["cabecera_derecha"]),
    ]]
    for indice, servicio in enumerate(datos.servicios, start=1):
        filas.append([
            Paragraph(str(indice), estilos["celda"]),
            Paragraph(servicio.nombre, estilos["celda"]),
            Paragraph(
                _formatear_monto(servicio.monto, datos.moneda),
                estilos["celda_derecha"],
            ),
        ])
    filas.append([
        "",
        Paragraph("TOTAL", estilos["celda_total"]),
        Paragraph(
            _formatear_monto(datos.monto_total, datos.moneda),
            estilos["celda_total"],
        ),
    ])

    tabla = Table(
        filas,
        colWidths=[ancho_total * 0.08, ancho_total * 0.74, ancho_total * 0.18],
        hAlign="LEFT",
        repeatRows=1,
    )
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), _AZUL),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -2), 0.25, _GRIS_LINEA),
        ("BOX", (0, 0), (-1, -1), 0.5, _AZUL_CLARO),
        ("LINEABOVE", (0, -1), (-1, -1), 0.75, _AZUL),
        ("BACKGROUND", (0, -1), (-1, -1), _FONDO_CABECERA),
    ]
    for indice in range(1, len(filas) - 1):
        if indice % 2 == 0:
            estilo.append(("BACKGROUND", (0, indice), (-1, indice), _FONDO_CABECERA))
    tabla.setStyle(TableStyle(estilo))
    return tabla


def _pie_de_pagina(canvas, documento) -> None:
    canvas.saveState()
    ancho, _ = A4
    canvas.setStrokeColor(_GRIS_LINEA)
    canvas.setLineWidth(0.5)
    canvas.line(20 * mm, 17 * mm, ancho - 20 * mm, 17 * mm)
    canvas.setFont("Helvetica-Oblique", 7.5)
    canvas.setFillColor(_GRIS_SUAVE)
    canvas.drawCentredString(
        ancho / 2,
        12.5 * mm,
        "Este documento es una constancia de pago y no constituye factura fiscal.",
    )
    canvas.drawString(20 * mm, 8.5 * mm, LIMITE_ZONA)
    canvas.drawRightString(ancho - 20 * mm, 8.5 * mm, f"Página {documento.page}")
    canvas.restoreState()


def generar_comprobante_pdf(datos: DatosComprobante) -> bytes:
    """Construye el PDF del comprobante y devuelve sus bytes en memoria."""
    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=16 * mm,
        bottomMargin=22 * mm,
        title=f"Comprobante de pago {datos.pago_id}",
        author="Clínica Oftalmológica",
        subject="Comprobante de pago",
    )
    estilos = _crear_estilos()
    ancho_total = A4[0] - 40 * mm

    elementos: list = []
    elementos.append(Paragraph("CLÍNICA OFTALMOLÓGICA", estilos["titulo"]))
    elementos.append(Paragraph("COMPROBANTE DE PAGO", estilos["subtitulo"]))
    elementos.append(Paragraph(
        f"Comprobante N° CP-{datos.pago_id:06d}",
        estilos["identificador"],
    ))
    elementos.append(Spacer(1, 4))
    elementos.append(HRFlowable(width="100%", thickness=1, color=_AZUL_CLARO))
    elementos.append(Spacer(1, 6))

    elementos.append(Paragraph("INFORMACIÓN DEL PAGO", estilos["seccion"]))
    elementos.append(_tabla_informacion(
        [
            ("N° interno del pago", str(datos.pago_id),
             "Estado", datos.estado),
            ("Fecha y hora del pago", _formatear_fecha_hora(datos.fecha_pago),
             "Fecha de emisión", _formatear_fecha_hora(datos.fecha_emision)),
            ("Método de pago", datos.metodo_pago,
             "Pasarela", datos.pasarela or "No registrada"),
            ("Moneda", datos.moneda,
             "Referencia de transacción",
             datos.referencia_transaccion or "No registrada"),
        ],
        estilos,
        ancho_total,
    ))

    elementos.append(Paragraph("INFORMACIÓN DEL PACIENTE", estilos["seccion"]))
    elementos.append(Paragraph(
        f"Nombres: {datos.paciente_nombres}<br/>"
        f"Apellidos: {datos.paciente_apellidos}",
        estilos["valor"],
    ))

    elementos.append(Paragraph("INFORMACIÓN DE LA CONSULTA", estilos["seccion"]))
    consulta_texto = (
        f"Consulta clínica N°: {datos.consulta_clinica_id}"
        if datos.consulta_clinica_id is not None
        else "Consulta clínica: No asociada"
    )
    elementos.append(Paragraph(
        f"{consulta_texto}<br/>"
        f"Fecha de la consulta: {_formatear_fecha_hora(datos.consulta_fecha)}",
        estilos["valor"],
    ))

    elementos.append(Spacer(1, 8))
    elementos.append(Paragraph("DETALLE DE SERVICIOS", estilos["seccion"]))
    elementos.append(_tabla_servicios(datos, estilos, ancho_total))

    elementos.append(Paragraph(
        "Este documento es una constancia de pago y no constituye factura "
        f"fiscal. No reemplaza a un documento tributario. {LIMITE_ZONA}.",
        estilos["nota"],
    ))

    documento.build(
        elementos,
        onFirstPage=_pie_de_pagina,
        onLaterPages=_pie_de_pagina,
    )
    return buffer.getvalue()
