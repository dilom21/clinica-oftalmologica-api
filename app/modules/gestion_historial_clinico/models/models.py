from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class HistorialClinico(Base):
    __tablename__ = "historial_clinico"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    paciente_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("paciente.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    fecha_apertura: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    observaciones_generales: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    antecedentes: Mapped[list["AntecedenteClinico"]] = relationship(
        back_populates="historial",
        order_by="AntecedenteClinico.fecha_registro.desc()",
    )


class AntecedenteClinico(Base):
    __tablename__ = "antecedente_clinico"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    historial_clinico_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("historial_clinico.id", ondelete="CASCADE"),
        nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False)

    historial: Mapped[HistorialClinico] = relationship(
        back_populates="antecedentes",
    )