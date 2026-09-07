from datetime import date, datetime

from pydantic import BaseModel, ConfigDict,Field

class PacienteCrear(BaseModel):
    usuario_id: int | None = None

    nombres: str
    apellidos: str

    ci: str | None = None
    fecha_nacimiento: date | None = None
    sexo: str | None = None

    telefono: str | None = None
    contacto_emergencia: str | None = None

    direccion: str | None = None

    estado: bool = True


class PacienteActualizar(BaseModel):
    usuario_id: int | None = None

    nombres: str | None = None
    apellidos: str | None = None

    ci: str | None = None
    fecha_nacimiento: date | None = None
    sexo: str | None = None

    telefono: str | None = None
    contacto_emergencia: str | None = None

    direccion: str | None = None

    estado: bool | None = None


class PacienteRespuesta(BaseModel):
    id: int

    usuario_id: int | None

    nombres: str
    apellidos: str

    ci: str | None
    fecha_nacimiento: date | None
    sexo: str | None

    telefono: str | None
    contacto_emergencia: str | None

    fecha_registro: datetime

    direccion: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)
class AntecedenteClinicoCrear(BaseModel):
    historial_clinico_id: int
    tipo: str = Field(..., max_length=30, description="ALERGIA, ENFERMEDAD, CIRUGIA, MEDICAMENTO, ANTECEDENTE_FAMILIAR, OTRO")
    descripcion: str = Field(..., min_length=2, description="Detalle del antecedente clínico")
    estado: bool = True


class AntecedenteClinicoActualizar(BaseModel):
    tipo: str | None = Field(None, max_length=30)
    descripcion: str | None = Field(None, min_length=2)
    estado: bool | None = None


class AntecedenteClinicoRespuesta(BaseModel):
    id: int
    historial_clinico_id: int
    tipo: str
    descripcion: str
    estado: bool
    fecha_registro: datetime

    model_config = ConfigDict(from_attributes=True)