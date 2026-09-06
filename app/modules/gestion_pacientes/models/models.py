from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Paciente(Base):
    __tablename__ = "paciente"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    usuario_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("usuario.id", ondelete="SET NULL"),
        unique=True
    )

    nombres: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    apellidos: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    ci: Mapped[str | None] = mapped_column(
        String(30)
    )

    fecha_nacimiento: Mapped[date | None] = mapped_column(
        Date
    )

    sexo: Mapped[str | None] = mapped_column(
        String(20)
    )

    telefono: Mapped[str | None] = mapped_column(
        String(30)
    )

    contacto_emergencia: Mapped[str | None] = mapped_column(
        String(150)
    )

    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )

    direccion: Mapped[str | None] = mapped_column(
        String(255)
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )