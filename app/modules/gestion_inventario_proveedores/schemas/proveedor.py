from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


RazonSocial = Annotated[str, Field(min_length=1, max_length=150)]
Nit = Annotated[str, Field(max_length=30)]
Telefono = Annotated[str, Field(max_length=20)]
Direccion = Annotated[str, Field(max_length=255)]
Contacto = Annotated[str, Field(max_length=100)]

_OPCIONALES = ("nit", "telefono", "correo", "direccion", "contacto")


class _ProveedorEntrada(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("razon_social", mode="before", check_fields=False)
    @classmethod
    def validar_razon_social(cls, valor):
        if isinstance(valor, str) and not valor.strip():
            raise ValueError("La razon social no puede estar vacia")
        return valor

    @field_validator(*_OPCIONALES, mode="before", check_fields=False)
    @classmethod
    def normalizar_opcionales(cls, valor):
        # Una cadena en blanco se guarda como NULL: evita choques del indice
        # unico de NIT con valores vacios.
        if isinstance(valor, str):
            return valor.strip() or None
        return valor

    @field_validator("correo", mode="after", check_fields=False)
    @classmethod
    def validar_longitud_correo(cls, valor):
        if valor is not None and len(valor) > 100:
            raise ValueError("El correo no puede superar 100 caracteres")
        return valor


class ProveedorCrear(_ProveedorEntrada):
    razon_social: RazonSocial
    nit: Nit | None = None
    telefono: Telefono | None = None
    correo: EmailStr | None = None
    direccion: Direccion | None = None
    contacto: Contacto | None = None
    estado: bool = True


class ProveedorActualizar(_ProveedorEntrada):
    razon_social: RazonSocial | None = None
    nit: Nit | None = None
    telefono: Telefono | None = None
    correo: EmailStr | None = None
    direccion: Direccion | None = None
    contacto: Contacto | None = None
    estado: bool | None = None

    @model_validator(mode="after")
    def validar_actualizacion_parcial(self):
        if not self.model_fields_set:
            raise ValueError("Debe enviar al menos un campo para actualizar")
        for campo in ("razon_social", "estado"):
            if campo in self.model_fields_set and getattr(self, campo) is None:
                raise ValueError(f"{campo} no admite null")
        return self


class ProveedorRespuesta(BaseModel):
    id: int
    razon_social: str
    nit: str | None
    telefono: str | None
    correo: str | None
    direccion: str | None
    contacto: str | None
    estado: bool | None

    model_config = ConfigDict(from_attributes=True)
