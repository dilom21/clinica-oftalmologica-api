from datetime import date, datetime
from sqlalchemy import Text, TIMESTAMP, ForeignKey, String, func

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
class HistorialClinico(Base):
    __tablename__ = "historial_clinico"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )

    paciente_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("paciente.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    fecha_apertura: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    observaciones_generales: Mapped[str | None] = mapped_column(
        Text
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )


class AntecedenteClinico(Base):
    __tablename__ = "antecedente_clinico"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )

    historial_clinico_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("historial_clinico.id", ondelete="CASCADE"),
        nullable=False
    )

    tipo: Mapped[str] = mapped_column(
        String(30),
        nullable=False
    )  # Ej: ALERGIA, ENFERMEDAD, CIRUGIA, MEDICAMENTO, etc.

    descripcion: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )