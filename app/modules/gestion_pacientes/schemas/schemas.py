from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

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


class MiPerfilPacienteActualizar(BaseModel):
    nombres: str | None = None
    apellidos: str | None = None
    fecha_nacimiento: date | None = None
    sexo: str | None = None
    telefono: str | None = None
    contacto_emergencia: str | None = None
    direccion: str | None = None


class MiPerfilPacienteRespuesta(BaseModel):
    id: int
    correo: str
    nombres: str
    apellidos: str
    ci: str | None
    fecha_nacimiento: date | None
    sexo: str | None
    telefono: str | None
    contacto_emergencia: str | None
    direccion: str | None
    estado: bool


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