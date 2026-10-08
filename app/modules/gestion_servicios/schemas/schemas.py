from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


PrecioCatalogo = Annotated[
    Decimal,
    Field(ge=0, max_digits=10, decimal_places=2, allow_inf_nan=False),
]
DuracionEstimada = Annotated[int, Field(gt=0, le=2**31 - 1)]


class _ServicioEntrada(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("nombre", mode="before", check_fields=False)
    @classmethod
    def validar_nombre(cls, valor):
        if isinstance(valor, str) and not valor.strip():
            raise ValueError("El nombre no puede estar vacio")
        return valor

    @field_validator("descripcion", mode="before", check_fields=False)
    @classmethod
    def normalizar_descripcion(cls, valor):
        if isinstance(valor, str):
            return valor.strip() or None
        return valor

    @field_validator("precio_base", mode="before", check_fields=False)
    @classmethod
    def rechazar_precio_booleano(cls, valor):
        if isinstance(valor, bool):
            raise ValueError("El precio base debe ser un numero")
        return valor

    @field_validator("duracion_estimada", mode="before", check_fields=False)
    @classmethod
    def rechazar_duracion_booleana(cls, valor):
        if isinstance(valor, bool):
            raise ValueError("La duracion estimada debe ser un entero")
        return valor


class ServicioCrear(_ServicioEntrada):
    nombre: str = Field(min_length=1, max_length=150)
    descripcion: str | None = Field(default=None, max_length=500)
    precio_base: PrecioCatalogo
    duracion_estimada: DuracionEstimada | None = None
    estado: bool = True


class ServicioActualizar(_ServicioEntrada):
    nombre: str | None = Field(default=None, min_length=1, max_length=150)
    descripcion: str | None = Field(default=None, max_length=500)
    precio_base: PrecioCatalogo | None = None
    duracion_estimada: DuracionEstimada | None = None
    estado: bool | None = None

    @model_validator(mode="after")
    def validar_actualizacion_parcial(self):
        if not self.model_fields_set:
            raise ValueError("Debe enviar al menos un campo para actualizar")
        for campo in ("nombre", "precio_base", "estado"):
            if campo in self.model_fields_set and getattr(self, campo) is None:
                raise ValueError(f"{campo} no admite null")
        return self


class ServicioRespuesta(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    precio_base: Decimal | None = Field(validation_alias="precio")
    duracion_estimada: int | None = Field(validation_alias="duracion")
    estado: bool

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("precio_base", when_used="json")
    def serializar_precio(self, valor: Decimal | None) -> float | None:
        # El dominio conserva Decimal; el JSON mantiene el contrato numerico de Angular.
        return float(valor) if valor is not None else None
