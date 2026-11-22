"""Selección y creación transaccional de recordatorios CU39 en un tenant."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.gestion_historial_clinico.models.controles import ControlMedico
from app.modules.notificaciones_interaccion_chatbot.services.notificaciones import crear_desde_evento

ZONA_CLINICA = ZoneInfo("America/La_Paz")


@dataclass(frozen=True)
class ConfiguracionRecordatorios:
    horas_cita: int = 24
    hora_control: time = time(9, 0)

    def __post_init__(self):
        if not 1 <= self.horas_cita <= 168:
            raise ValueError("horas_cita debe estar entre 1 y 168")


def procesar_tenant(
    db: Session,
    *,
    ahora: datetime,
    configuracion: ConfiguracionRecordatorios = ConfiguracionRecordatorios(),
) -> int:
    """Usa la sesión de una sola clínica; el llamador confirma o revierte."""
    if ahora.tzinfo is None or ahora.utcoffset() is None:
        raise ValueError("ahora debe incluir zona horaria")
    local = ahora.astimezone(ZONA_CLINICA)
    fin_ventana = local + timedelta(hours=configuracion.horas_cita)
    creadas = 0

    citas = db.scalars(
        select(Cita).where(
            Cita.fecha >= local.date(),
            Cita.fecha <= fin_ventana.date(),
            Cita.estado.in_(("PROGRAMADA", "CONFIRMADA", "EN_ESPERA")),
        ).order_by(Cita.fecha, Cita.hora_inicio, Cita.id).with_for_update(skip_locked=True)
    ).all()
    for cita in citas:
        inicio = datetime.combine(cita.fecha, cita.hora_inicio, ZONA_CLINICA)
        if not local <= inicio <= fin_ventana:
            continue
        clave = f"recordatorio:cita:{cita.id}:{cita.fecha.isoformat()}:{cita.hora_inicio.strftime('%H%M')}"
        _, creada = crear_desde_evento(
            db, paciente_id=cita.paciente_id, titulo="Recordatorio de cita",
            mensaje=f"Tienes una cita el {cita.fecha.isoformat()} a las {cita.hora_inicio.strftime('%H:%M')}.",
            tipo="RECORDATORIO_CITA", clave_evento=clave,
            fecha_envio=ahora, con_estado=True,
        )
        creadas += int(creada)

    siguiente_fecha = local.date() + timedelta(days=1)
    umbral = datetime.combine(local.date(), configuracion.hora_control, ZONA_CLINICA)
    if local >= umbral:
        controles = db.scalars(
            select(ControlMedico).where(
                ControlMedico.fecha_programada == siguiente_fecha,
                ControlMedico.estado == "PROGRAMADO",
            ).order_by(ControlMedico.id).with_for_update(skip_locked=True)
        ).all()
        for control in controles:
            clave = f"recordatorio:control:{control.id}:{control.fecha_programada.isoformat()}"
            _, creada = crear_desde_evento(
                db, paciente_id=control.paciente_id,
                titulo="Recordatorio de control médico",
                mensaje=f"Tienes un control médico programado para el {control.fecha_programada.isoformat()}.",
                tipo="RECORDATORIO_CONTROL", clave_evento=clave,
                fecha_envio=ahora, con_estado=True,
            )
            creadas += int(creada)
    return creadas
