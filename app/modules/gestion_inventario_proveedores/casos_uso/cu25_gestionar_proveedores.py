"""Entrada compatible del CU28 (nombre histórico del archivo: CU25).

La implementación está separada en models, schemas, repositories, services
y api. Este módulo reexporta el contrato público anterior sin duplicar modelos
ni rutas. Para integrar el módulo completo se debe registrar el router de
gestion_inventario_proveedores.api.router en app.main, previa autorización.
No se ejecutan migraciones ni se crean tablas al importar.
"""

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
from app.modules.gestion_inventario_proveedores.models.models import Proveedor
from app.modules.gestion_inventario_proveedores.schemas.schemas import (
    ProveedorActualizar,
    ProveedorCrear,
    ProveedorOperacion,
    ProveedorRespuesta,
    ProveedoresPagina,
)

__all__ = [
    "Proveedor", "ProveedorActualizar", "ProveedorCrear", "ProveedorOperacion",
    "ProveedorRespuesta", "ProveedoresPagina", "actualizar_proveedor",
    "consultar_proveedor", "dar_baja_proveedor", "listar_proveedores",
    "permiso_escritura", "permiso_lectura", "registrar_proveedor", "router",
]