"""CU26 - Contratos Pydantic para categorias, productos y lotes."""
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NombreCategoria = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
CodigoProducto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
NombreProducto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
UnidadMedida = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
NumeroLote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
IdPositivo = Annotated[int, Field(gt=0, le=2**63 - 1)]


class CategoriaCrear(BaseModel):
    nombre: NombreCategoria
    descripcion: str | None = None


class CategoriaActualizar(CategoriaCrear):
    pass


class CategoriaRespuesta(CategoriaCrear):
    model_config = ConfigDict(from_attributes=True)
    id: int
    estado: bool
    fecha_creacion: datetime


class ProductoCrear(BaseModel):
    categoria_id: IdPositivo
    codigo: CodigoProducto
    nombre: NombreProducto
    descripcion: str | None = None
    unidad_medida: UnidadMedida = "UNIDAD"
    stock_minimo: int = Field(default=0, ge=0, le=2147483647)


class ProductoActualizar(ProductoCrear):
    pass


class ProductoRespuesta(ProductoCrear):
    model_config = ConfigDict(from_attributes=True)
    id: int
    estado: bool
    fecha_creacion: datetime
    fecha_actualizacion: datetime


class LoteBase(BaseModel):
    numero_lote: NumeroLote
    fecha_fabricacion: date | None = None
    fecha_vencimiento: date | None = None
    observaciones: str | None = None

    @model_validator(mode="after")
    def fechas_validas(self):
        if self.fecha_fabricacion and self.fecha_vencimiento and self.fecha_vencimiento < self.fecha_fabricacion:
            raise ValueError("La fecha de vencimiento debe ser igual o posterior a la fabricación")
        return self


class LoteCrear(LoteBase):
    producto_id: IdPositivo


class LoteActualizar(LoteBase):
    """No permite trasladar un lote de producto por trazabilidad."""
    pass


class LoteRespuesta(LoteCrear):
    model_config = ConfigDict(from_attributes=True)
    id: int
    estado: bool
    fecha_creacion: datetime


class EstadoActualizar(BaseModel):
    estado: bool
