"""CU29: registrar compras a proveedor (nombre histórico del archivo: CU26).

La implementación se organiza en models/compras.py, schemas/compras.py,
repositories/compras.py, services/compras.py y api/compras.py. El router del
módulo ya incluye estas rutas; este archivo reexporta el contrato del caso
de uso sin duplicar modelos ni registrar rutas adicionales.

Se utiliza exclusivamente la base de la empresa resuelta desde el token.
La compra se recibe en una sola transacción junto con sus detalles, lotes,
existencias, movimientos y auditoría. No se ejecuta DDL ni migraciones.

El esquema exige un lote por detalle: enviar lote_id o numero_lote (para
crear un lote o reutilizarlo por producto y número). Las fechas existentes
se conservan y se rechazan discrepancias, lotes vencidos e inactivos.
Las cantidades son enteros positivos; costo_unitario admite cero y dos
decimales (NUMERIC(12, 2)). Subtotales y total se calculan en el servidor.

POST /compras/ requiere permiso de ESCRITURA para la función
«Registrar compras a proveedor». Los GET requieren LECTURA; AMBAS habilita
las dos acciones. No se crean ni se asignan permisos automáticamente.
"""

from app.modules.gestion_inventario_proveedores.api.compras import (
    consultar_compra, listar_compras, listar_lotes, listar_productos,
    listar_proveedores, permiso_escritura, permiso_lectura, registrar_compra, router,
)
from app.modules.gestion_inventario_proveedores.schemas.compras import (
    CompraCrear, CompraOperacion, CompraRespuesta, CompraResumen, ComprasPagina,
    DetalleCompraCrear, DetalleCompraRespuesta, LoteRespuesta, LotesPagina,
    ProductoRespuesta, ProductosPagina,
)
from app.modules.gestion_inventario_proveedores.services.compras import (
    consultar, crear, listar,
)

__all__ = [
    "CompraCrear", "CompraOperacion", "CompraRespuesta", "CompraResumen", "ComprasPagina",
    "DetalleCompraCrear", "DetalleCompraRespuesta", "LoteRespuesta", "LotesPagina",
    "ProductoRespuesta", "ProductosPagina", "consultar", "crear", "listar",
    "consultar_compra", "listar_compras", "listar_lotes", "listar_productos",
    "listar_proveedores", "permiso_escritura", "permiso_lectura", "registrar_compra", "router",
]
