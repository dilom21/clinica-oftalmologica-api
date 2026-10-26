"""CU25 - Consultar información clínica autorizada.

Contrato de solo lectura para el paciente autenticado. No incluye datos de
otros pacientes, registros inactivos ni referencias internas de archivos.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class OftalmologoClinicoResumen(BaseModel):
    id: int
    nombres: str
    apellidos: str
    especialidad: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AntecedenteAutorizadoRespuesta(BaseModel):
    id: int
    tipo: str
    descripcion: str
    fecha_registro: datetime

    model_config = ConfigDict(from_attributes=True)


class DiagnosticoAutorizadoRespuesta(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    fecha_diagnostico: datetime

    model_config = ConfigDict(from_attributes=True)


class TratamientoAutorizadoRespuesta(BaseModel):
    id: int
    descripcion: str
    observaciones: str | None
    fecha_inicio: date | None
    fecha_fin: date | None

    model_config = ConfigDict(from_attributes=True)


class IndicacionAutorizadaRespuesta(BaseModel):
    id: int
    descripcion: str
    fecha_registro: datetime

    model_config = ConfigDict(from_attributes=True)


class DetalleRecetaAutorizadoRespuesta(BaseModel):
    id: int
    medicamento: str
    presentacion: str | None
    dosis: str | None
    frecuencia: str | None
    duracion: str | None
    indicaciones: str | None

    model_config = ConfigDict(from_attributes=True)


class RecetaAutorizadaRespuesta(BaseModel):
    id: int
    fecha_emision: datetime
    observaciones: str | None
    detalles: list[DetalleRecetaAutorizadoRespuesta]

    model_config = ConfigDict(from_attributes=True)


class ResultadoExamenAutorizadoRespuesta(BaseModel):
    id: int
    fecha_resultado: datetime
    resultado: str

    model_config = ConfigDict(from_attributes=True)


class ExamenAutorizadoRespuesta(BaseModel):
    id: int
    nombre_examen: str
    fecha_solicitud: datetime
    observaciones: str | None
    resultados: list[ResultadoExamenAutorizadoRespuesta]

    model_config = ConfigDict(from_attributes=True)


class ConsultaAutorizadaRespuesta(BaseModel):
    id: int
    fecha_consulta: datetime
    motivo_consulta: str | None
    anamnesis: str | None
    observaciones: str | None
    oftalmologo: OftalmologoClinicoResumen
    diagnosticos: list[DiagnosticoAutorizadoRespuesta]
    tratamientos: list[TratamientoAutorizadoRespuesta]
    indicaciones: list[IndicacionAutorizadaRespuesta]
    recetas: list[RecetaAutorizadaRespuesta]
    examenes: list[ExamenAutorizadoRespuesta]


class ControlAutorizadoRespuesta(BaseModel):
    id: int
    fecha_programada: date
    motivo: str | None
    observaciones: str | None
    estado: str | None
    oftalmologo: OftalmologoClinicoResumen


class InformacionClinicaAutorizadaRespuesta(BaseModel):
    historial_id: int | None
    fecha_apertura: datetime | None
    observaciones_generales: str | None
    antecedentes: list[AntecedenteAutorizadoRespuesta]
    consultas: list[ConsultaAutorizadaRespuesta]
    controles: list[ControlAutorizadoRespuesta]
