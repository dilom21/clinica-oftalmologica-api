"""Mapeo del esquema existente; este módulo no ejecuta DDL."""

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Proveedor(Base):
    __tablename__ = "proveedores"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    razon_social: Mapped[str] = mapped_column(String(150), nullable=False)
    nit: Mapped[str | None] = mapped_column(String(30), unique=True, index=True)
    telefono: Mapped[str | None] = mapped_column(String(20))
    correo: Mapped[str | None] = mapped_column(String(100))
    direccion: Mapped[str | None] = mapped_column(String(255))
    contacto: Mapped[str | None] = mapped_column(String(100))
    estado: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)