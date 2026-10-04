from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Identity, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ServicioOftalmologico(Base):
    """Catálogo referenciado por servicio_realizado en Supabase.

    Se conservan los atributos de CU21 y su contrato HTTP; únicamente se
    mapean a las columnas físicas precio y duracion del diseño original.
    """

    __tablename__ = "servicio_oftalmologico"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    precio_base: Mapped[Decimal | None] = mapped_column("precio", Numeric(10, 2))
    duracion_estimada: Mapped[int | None] = mapped_column("duracion", Integer)
    estado: Mapped[bool | None] = mapped_column(Boolean, default=True)
