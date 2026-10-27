from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool, field_validator


class _EntradaProveedor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def limpiar_texto(cls, valor):
        if isinstance(valor, str):
            valor = valor.strip()
            if "\x00" in valor:
                raise ValueError("El campo no puede contener caracteres nulos")
            return valor or None
        return valor


class ProveedorCrear(_EntradaProveedor):
    razon_social: str = Field(min_length=1, max_length=150)
    nit: str | None = Field(default=None, max_length=30)
    telefono: str | None = Field(default=None, max_length=20)
    correo: EmailStr | None = Field(default=None, max_length=100)
    direccion: str | None = Field(default=None, max_length=255)
    contacto: str | None = Field(default=None, max_length=100)


class ProveedorActualizar(_EntradaProveedor):
    razon_social: str | None = Field(default=None, min_length=1, max_length=150)
    nit: str | None = Field(default=None, max_length=30)
    telefono: str | None = Field(default=None, max_length=20)
    correo: EmailStr | None = Field(default=None, max_length=100)
    direccion: str | None = Field(default=None, max_length=255)
    contacto: str | None = Field(default=None, max_length=100)
    estado: StrictBool | None = None

    @field_validator("razon_social", "estado")
    @classmethod
    def rechazar_nulos_explicitos(cls, valor):
        # Los campos omitidos no se validan ni se incluyen en la actualización.
        if valor is None:
            raise ValueError("El campo no puede estar vacío ni ser nulo")
        return valor


class ProveedorRespuesta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    razon_social: str
    nit: str | None
    telefono: str | None
    correo: str | None
    direccion: str | None
    contacto: str | None
    estado: bool | None


class ProveedoresPagina(BaseModel):
    items: list[ProveedorRespuesta]
    total: int
    pagina: int
    tamanio: int = 20


class ProveedorOperacion(BaseModel):
    mensaje: str
    proveedor: ProveedorRespuesta