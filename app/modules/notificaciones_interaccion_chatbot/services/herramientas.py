"""Explicit read-only registry. Identity is never an argument supplied by AI."""
from datetime import date, datetime, time, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, field_validator

from app.core.time import fecha_local_aplicacion
from app.core.config import APP_TIMEZONE
from zoneinfo import ZoneInfo
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.database import session as sessions
from app.modules.gestion_agenda_citas.services import service as agenda, citas
from app.modules.gestion_agenda_citas.schemas.schemas import DURACION_CITA_MINUTOS
from app.modules.gestion_servicios.services import catalogo
from app.modules.notificaciones_interaccion_chatbot.schemas.chatbot import (
    Entrada, ClinicaTarjeta, ServiciosTarjeta, ServicioPublico,
    ProfesionalesTarjeta, ProfesionalPublico, DisponibilidadTarjeta,
    Intervalo, PropuestaTarjeta, CitasTarjeta, CitaPropia, AccionTarjeta,
)
from app.modules.notificaciones_interaccion_chatbot.services.privacidad import respuesta_local


class Vacio(Entrada):
    pass


class Buscar(Entrada):
    busqueda: str = Field(default="", max_length=150)
    offset: int = Field(default=0, ge=0, le=1000, strict=True)
    limite: int = Field(default=5, ge=1, le=10, strict=True)


class Detalle(Entrada):
    servicio_id: int = Field(gt=0, le=2**63 - 1, strict=True)


class Profesionales(Entrada):
    limite: int = Field(default=5, ge=1, le=10, strict=True)


class Disponibilidad(Entrada):
    oftalmologo_id: int = Field(gt=0, le=2**63 - 1, strict=True)
    fecha: date = Field(strict=True)

    @field_validator("fecha")
    @classmethod
    def ventana(cls, value):
        hoy = fecha_local_aplicacion()
        if not hoy <= value <= hoy + timedelta(days=90):
            raise ValueError("fecha fuera del horizonte de consulta")
        return value


class Propuesta(Disponibilidad):
    hora_inicio: time

    @field_validator("hora_inicio")
    @classmethod
    def hora_local(cls, value):
        if value.tzinfo or value.second or value.microsecond:
            raise ValueError("hora local con precisión de minutos requerida")
        return value


class MisCitas(Entrada):
    limite: int = Field(default=5, ge=1, le=10, strict=True)


class Navegacion(Entrada):
    destino: Literal["catalogo", "agendar_cita", "mis_citas", "detalle_cita", "reprogramar_cita", "cancelar_cita"]
    cita_id: int | None = Field(default=None, gt=0, le=2**63 - 1, strict=True)


REGISTRO = {
    "consultar_clinica": (Vacio, "Información institucional validada. Horario general no registrado."),
    "consultar_servicios": (Buscar, "Buscar servicios activos de la clínica actual; preguntar si hay ambigüedad."),
    "consultar_detalle_servicio": (Detalle, "Consultar un servicio activo; nunca inventar precios."),
    "consultar_profesionales": (Profesionales, "Listar alternativas de profesionales activos."),
    "consultar_disponibilidad": (Disponibilidad, "Consultar intervalos libres reales de un profesional y una fecha; solicitar los datos faltantes."),
    "preparar_solicitud_cita": (Propuesta, "Validar horario y preparar formulario. NO reserva ni registra una cita."),
    "consultar_mis_citas": (MisCitas, "Próximas citas propias mediante tarjeta privada local, sin datos al modelo."),
    "orientar_navegacion": (Navegacion, "Abrir una pantalla autorizada. Nunca ejecutar cambios; ID solo de cita propia."),
}


def definiciones():
    return [{"type": "function", "function": {"name": nombre, "description": desc,
             "parameters": modelo.model_json_schema()}}
            for nombre, (modelo, desc) in REGISTRO.items()]


def _servicio(row):
    return ServicioPublico(id=row.id, nombre=row.nombre[:150],
                          descripcion=row.descripcion[:500] if row.descripcion else None,
                          precio=row.precio, duracion_minutos=row.duracion)


def ejecutar(nombre, args, db, actor):
    """Only call after REGISTRO validation. Returns typed cards, never ORM objects."""
    if nombre == "consultar_clinica":
        with sessions.open_control_session() as control:
            empresa = TenantControlPlaneRepository(control).get_empresa_by_codigo(actor.codigo)
            if empresa is None or empresa.id != actor.empresa or empresa.estado != "ACTIVA":
                raise HTTPException(404, "Información no disponible")
            return ClinicaTarjeta(nombre=empresa.nombre_comercial, telefono=empresa.telefono,
                                  correo=empresa.correo, direccion=empresa.direccion)
    if nombre in {"consultar_servicios", "consultar_detalle_servicio"}:
        rows = (catalogo.listar(db, **args.model_dump()) if nombre == "consultar_servicios"
                else [catalogo.detalle(db, args.servicio_id)])
        return ServiciosTarjeta(servicios=[_servicio(r) for r in rows])
    if nombre == "consultar_profesionales":
        rows = agenda.listar_oftalmologos_activos(db, usuario=actor.usuario, limite=args.limite)
        return ProfesionalesTarjeta(profesionales=[ProfesionalPublico(
            id=r.id, nombre=f"{r.nombres} {r.apellidos}", especialidad=r.especialidad)
            for r in rows[:args.limite]])
    if nombre in {"consultar_disponibilidad", "preparar_solicitud_cita"}:
        result = agenda.consultar_disponibilidad(db, args.oftalmologo_id, args.fecha)
        ahora = datetime.now(ZoneInfo(APP_TIMEZONE))
        disponibles = [i for i in result.intervalos_disponibles
                       if args.fecha > ahora.date() or i.hora_fin > ahora.time()]
        if nombre == "consultar_disponibilidad":
            return DisponibilidadTarjeta(oftalmologo_id=args.oftalmologo_id, fecha=args.fecha,
                intervalos=[Intervalo(hora_inicio=max(i.hora_inicio, ahora.time().replace(second=0, microsecond=0))
                                     if args.fecha == ahora.date() else i.hora_inicio, hora_fin=i.hora_fin)
                            for i in disponibles[:48]])
        inicio = datetime.combine(args.fecha, args.hora_inicio)
        fin = inicio + timedelta(minutes=DURACION_CITA_MINUTOS)
        if (args.fecha == ahora.date() and args.hora_inicio <= ahora.time()) or fin.date() != args.fecha or not any(
            i.hora_inicio <= args.hora_inicio and fin.time() <= i.hora_fin
            for i in result.intervalos_disponibles
        ):
            raise HTTPException(409, "Horario no disponible")
        return PropuestaTarjeta(oftalmologo_id=args.oftalmologo_id, fecha=args.fecha,
                               hora_inicio=args.hora_inicio, hora_fin=fin.time())
    if nombre == "consultar_mis_citas":
        rows = citas.listar_citas(db, usuario=actor.usuario, limite=args.limite,
                                  fecha_desde=fecha_local_aplicacion(),
                                  estados=("PROGRAMADA", "CONFIRMADA", "EN_ESPERA"))
        return CitasTarjeta(citas=[CitaPropia(id=r.id, oftalmologo_id=r.oftalmologo_id,
            fecha=r.fecha, hora_inicio=r.hora_inicio, hora_fin=r.hora_fin, estado=r.estado) for r in rows])
    if nombre == "orientar_navegacion":
        if args.destino in {"detalle_cita", "reprogramar_cita", "cancelar_cita"} and args.cita_id is None:
            raise HTTPException(422, "Indica una cita propia")
        if args.cita_id is not None:
            citas.obtener_cita(db, args.cita_id, usuario=actor.usuario)
        return AccionTarjeta(destino=args.destino, cita_id=args.cita_id)
    raise HTTPException(422, "Herramienta no autorizada")


def para_modelo(tarjeta):
    """Deliberately exclude private cards, institutional contacts and descriptions.

    Public IDs/names allow contextual professional/service selection. Money remains
    authoritative in cards, not in generated prose. Untrusted text is not instructions.
    """
    if tarjeta.privacidad == "privada":
        return {"estado": "informacion_entregada_localmente"}
    if isinstance(tarjeta, ServiciosTarjeta):
        return {"servicios": [{"id": r.id, "nombre": r.nombre}
                              for r in tarjeta.servicios if not respuesta_local(r.nombre)]}
    if isinstance(tarjeta, ProfesionalesTarjeta):
        return {"profesionales": [{"id": r.id, "nombre": r.nombre}
                                  for r in tarjeta.profesionales if not respuesta_local(r.nombre)]}
    if isinstance(tarjeta, DisponibilidadTarjeta):
        return tarjeta.model_dump(mode="json", exclude={"privacidad", "tipo"})
    if isinstance(tarjeta, PropuestaTarjeta):
        return tarjeta.model_dump(mode="json", exclude={"privacidad", "tipo"})
    return {"estado": "datos institucionales en tarjeta local", "horario_general": "no registrado"}
