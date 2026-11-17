"""Entrada historica compatible para CU28 - Gestionar proveedores."""

from app.modules.gestion_inventario_proveedores.api.proveedores import (
    actualizar_proveedor,
    consultar_proveedor,
    dar_baja_proveedor,
    listar_proveedores,
    permiso_escritura,
    permiso_lectura,
    registrar_proveedor,
    router,
)
from app.modules.gestion_inventario_proveedores.models.proveedor import Proveedor
from app.modules.gestion_inventario_proveedores.schemas.proveedor import (
    ProveedorActualizar,
    ProveedorCrear,
    ProveedorOperacion,
    ProveedorRespuesta,
    ProveedoresPagina,
)

__all__ = [
    "Proveedor",
    "ProveedorActualizar",
    "ProveedorCrear",
    "ProveedorOperacion",
    "ProveedorRespuesta",
    "ProveedoresPagina",
    "actualizar_proveedor",
    "consultar_proveedor",
    "dar_baja_proveedor",
    "listar_proveedores",
    "permiso_escritura",
    "permiso_lectura",
    "registrar_proveedor",
    "router",
]
