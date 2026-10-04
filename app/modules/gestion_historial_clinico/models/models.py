from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.modules.gestion_servicios.models.models import ServicioOftalmologico
from app.modules.gestion_agenda_citas.models.models import Cita, Oftalmologo
from app.modules.gestion_pacientes.models.models import Paciente


class HistorialClinico(Base):
    __tablename__ = "historial_clinico"
    __table_args__ = {'extend_existing': True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    paciente_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("paciente.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    fecha_apertura: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    observaciones_generales: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    paciente: Mapped[Paciente] = relationship()
    antecedentes: Mapped[list["AntecedenteClinico"]] = relationship(
        back_populates="historial",
        order_by=lambda: (
            AntecedenteClinico.fecha_registro.desc(), AntecedenteClinico.id.desc(),
        ),
    )


class AntecedenteClinico(Base):
    __tablename__ = "antecedente_clinico"
    __table_args__ = {'extend_existing': True} # <--- AGREGA ESTA LÍNEA AQUÍ
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    historial_clinico_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("historial_clinico.id", ondelete="CASCADE"),
        nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    historial: Mapped[HistorialClinico] = relationship(back_populates="antecedentes")


class ConsultaClinica(Base):
    __tablename__ = "consulta_clinica"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    historial_clinico_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("historial_clinico.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cita_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("cita.id", ondelete="SET NULL"),
        unique=True,
    )
    oftalmologo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("oftalmologo.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fecha_consulta: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    motivo_consulta: Mapped[str | None] = mapped_column(String(255))
    anamnesis: Mapped[str | None] = mapped_column(Text)
    observaciones: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    oftalmologo: Mapped[Oftalmologo] = relationship()
    cita: Mapped[Cita | None] = relationship()


class Diagnostico(Base):
    __tablename__ = "diagnostico"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consulta_clinica_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("consulta_clinica.id", ondelete="CASCADE"),
        nullable=False,
    )
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    fecha_diagnostico: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    consulta_clinica: Mapped[ConsultaClinica] = relationship()


class ServicioRealizado(Base):
    """Tabla clínica; precio_aplicado se agrega mediante la migración SQL CU22."""

    __tablename__ = "servicio_realizado"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    servicio_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("servicio_oftalmologico.id", ondelete="RESTRICT"),
        nullable=False,
    )
    paciente_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("paciente.id", ondelete="RESTRICT"), nullable=False,
    )
    consulta_clinica_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("consulta_clinica.id", ondelete="SET NULL"),
    )
    oftalmologo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("oftalmologo.id", ondelete="RESTRICT"), nullable=False,
    )
    fecha_realizacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    precio_aplicado: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    observaciones: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[bool | None] = mapped_column(Boolean, default=True)

    servicio: Mapped[ServicioOftalmologico] = relationship()
    paciente: Mapped[Paciente] = relationship()
    oftalmologo: Mapped[Oftalmologo] = relationship()
    consulta_clinica: Mapped[ConsultaClinica | None] = relationship()
