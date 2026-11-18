"""Generacion de la constancia de cita medica en PDF (CU37).

Construye el documento a partir de datos ya resueltos por la capa de
servicio. No consulta la base de datos ni modifica estado: es una operacion
de lectura pura. Reutiliza los estilos y helpers del comprobante de pagos
para mantener una imagen documental consistente entre clinicas (tenants).
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from app.modules.gestion_pagos.services.comprobante_pdf import (
    LIMITE_ZONA,
    _AZUL_CLARO,
    _GRIS_LINEA,
    _GRIS_SUAVE,
    _canvas_invariable,
    _crear_estilos,
    _formatear_fecha_hora,
    _tabla_informacion,
)


@dataclass(frozen=True)
class DatosConstancia:
    """Datos planos necesarios para construir la constancia."""

    clinica_nombre: str
    cita_id: int
    fecha_emision: datetime
    paciente_nombres: str
    paciente_apellidos: str
    paciente_ci: str | None
    oftalmologo_nombres: str
    oftalmologo_apellidos: str
    oftalmologo_matricula: str | None
    oftalmologo_especialidad: str | None
    fecha_cita: date
    hora_inicio: time
    hora_fin: time
    motivo: str | None
    estado: str


def numero_constancia(cita_id: int) -> str:
    return f"CC-{cita_id:06d}"


def _texto(valor: str | None, vacio: str = "No registrado") -> str:
    limpio = (valor or "").strip()
    return escape(limpio) if limpio else vacio


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
        "Constancia de atencion de cita medica. No constituye receta, "
        "diagnostico ni factura fiscal.",
    )
    canvas.drawString(20 * mm, 8.5 * mm, LIMITE_ZONA)
    canvas.drawRightString(ancho - 20 * mm, 8.5 * mm, f"Página {documento.page}")
    canvas.restoreState()


def generar_constancia_pdf(datos: DatosConstancia) -> bytes:
    """Construye el PDF de la constancia y devuelve sus bytes en memoria."""
    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=16 * mm,
        bottomMargin=22 * mm,
        title=f"Constancia de cita {numero_constancia(datos.cita_id)}",
        author=datos.clinica_nombre,
        subject="Constancia de cita médica",
    )
    estilos = _crear_estilos()
    ancho_total = A4[0] - 40 * mm

    paciente = f"{_texto(datos.paciente_nombres)} {_texto(datos.paciente_apellidos)}"
    medico = (
        f"{_texto(datos.oftalmologo_nombres)} "
        f"{_texto(datos.oftalmologo_apellidos)}"
    )

    elementos: list = [
        Paragraph(escape(datos.clinica_nombre.upper()), estilos["titulo"]),
        Paragraph("CONSTANCIA DE CITA MÉDICA", estilos["subtitulo"]),
        Paragraph(
            f"Constancia N° {numero_constancia(datos.cita_id)}",
            estilos["identificador"],
        ),
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=1, color=_AZUL_CLARO),
        Spacer(1, 6),
        Paragraph("DATOS DEL PACIENTE", estilos["seccion"]),
        _tabla_informacion(
            [("Paciente", paciente, "CI", _texto(datos.paciente_ci))],
            estilos,
            ancho_total,
        ),
        Paragraph("DATOS DE LA CITA", estilos["seccion"]),
        _tabla_informacion(
            [
                ("Fecha de la cita", datos.fecha_cita.strftime("%d/%m/%Y"),
                 "Horario",
                 f"{datos.hora_inicio.strftime('%H:%M')} - "
                 f"{datos.hora_fin.strftime('%H:%M')}"),
                ("Oftalmólogo", medico,
                 "Matrícula", _texto(datos.oftalmologo_matricula)),
                ("Especialidad", _texto(datos.oftalmologo_especialidad),
                 "Estado", escape(datos.estado)),
                ("Motivo", _texto(datos.motivo),
                 "Fecha de emisión",
                 _formatear_fecha_hora(datos.fecha_emision)),
            ],
            estilos,
            ancho_total,
        ),
        Spacer(1, 10),
        Paragraph(
            f"Se deja constancia de que {paciente} fue atendido/a en "
            f"{escape(datos.clinica_nombre)} el "
            f"{datos.fecha_cita.strftime('%d/%m/%Y')}, en el horario de "
            f"{datos.hora_inicio.strftime('%H:%M')} a "
            f"{datos.hora_fin.strftime('%H:%M')}, con el/la profesional "
            f"{medico}. Se extiende la presente a solicitud del interesado/a "
            f"para los fines que estime convenientes. {LIMITE_ZONA}.",
            estilos["valor"],
        ),
    ]

    documento.build(
        elementos,
        onFirstPage=_pie_de_pagina,
        onLaterPages=_pie_de_pagina,
        canvasmaker=_canvas_invariable,
    )
    return buffer.getvalue()
