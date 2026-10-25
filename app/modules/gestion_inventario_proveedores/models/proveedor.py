from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Proveedor(Base):
    # Refleja la tabla existente en cada base tenant. `compra.proveedor_id`
    # (CU26) la referencia, por eso se conserva el nombre plural y el id INTEGER.
    # La estructura se gestiona por migraciones SQL, nunca con create_all.
    __tablename__ = "proveedores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    razon_social: Mapped[str] = mapped_column(String(150), nullable=False)
    nit: Mapped[str | None] = mapped_column(String(30), unique=True, index=True)
    telefono: Mapped[str | None] = mapped_column(String(20))
    correo: Mapped[str | None] = mapped_column(String(100))
    direccion: Mapped[str | None] = mapped_column(String(255))
    contacto: Mapped[str | None] = mapped_column(String(100))
    estado: Mapped[bool | None] = mapped_column(Boolean, default=True)
