"""Contratos CU27: el usuario, los saldos y la procedencia los fija el servidor."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Annotated


TipoMovimiento = Literal[
    "STOCK_INICIAL", "ENTRADA_COMPRA", "SALIDA_USO",
    "AJUSTE_ENTRADA", "AJUSTE_SALIDA", "DEVOLUCION_ENTRADA",
    "DEVOLUCION_PROVEEDOR", "SALIDA_VENCIMIENTO",
]
TipoManual = Literal[
    "STOCK_INICIAL", "SALIDA_USO", "AJUSTE_ENTRADA",
    "AJUSTE_SALIDA", "SALIDA_VENCIMIENTO",
]
IdPositivo = Annotated[int, Field(gt=0, le=2**63 - 1)]
CantidadPositiva = Annotated[int, Field(gt=0, le=2_147_483_647)]
TextoOpcional = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class MovimientoManualCrear(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    lote_id: IdPositivo
    tipo: TipoManual
    cantidad: CantidadPositiva
    observaciones: TextoOpcional | None = None


class MovimientoRespuesta(BaseModel):
    id: int
    inventario_id: int
    lote_id: int
    usuario_id: int | None
    tipo: TipoMovimiento
    cantidad: int
    stock_anterior: int
    stock_posterior: int
    fecha_hora: datetime
    observaciones: str | None
    referencia_tipo: str | None
    referencia_id: int | None


class ExistenciaRespuesta(BaseModel):
    lote_id: int
    inventario_id: int | None
    stock_actual: int
    stock_reservado: int
    stock_disponible: int
