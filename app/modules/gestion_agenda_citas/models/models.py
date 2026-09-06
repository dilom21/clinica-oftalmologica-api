from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Oftalmologo(Base):
    __tablename__ = "oftalmologo"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    usuario_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("usuario.id"),
        unique=True,
        nullable=False
    )

    matricula: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False
    )

    nombres: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    apellidos: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    especialidad: Mapped[str | None] = mapped_column(
        String(120)
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )


class HorarioOftalmologo(Base):
    __tablename__ = "horario_oftalmologo"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    oftalmologo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("oftalmologo.id"),
        nullable=False
    )

    dia_semana: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False
    )

    hora_inicio: Mapped[time] = mapped_column(
        Time,
        nullable=False
    )

    hora_fin: Mapped[time] = mapped_column(
        Time,
        nullable=False
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )


class BloqueoHorario(Base):
    __tablename__ = "bloqueo_horario"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    oftalmologo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("oftalmologo.id"),
        nullable=False
    )

    fecha: Mapped[date] = mapped_column(
        Date,
        nullable=False
    )

    hora_inicio: Mapped[time] = mapped_column(
        Time,
        nullable=False
    )

    hora_fin: Mapped[time] = mapped_column(
        Time,
        nullable=False
    )

    motivo: Mapped[str | None] = mapped_column(
        String(255)
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )


class Cita(Base):
    __tablename__ = "cita"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    paciente_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("paciente.id"),
        nullable=False
    )

    oftalmologo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("oftalmologo.id"),
        nullable=False
    )

    fecha: Mapped[date] = mapped_column(
        Date,
        nullable=False
    )

    hora_inicio: Mapped[time] = mapped_column(
        Time,
        nullable=False
    )

    hora_fin: Mapped[time] = mapped_column(
        Time,
        nullable=False
    )

    motivo: Mapped[str | None] = mapped_column(
        String(255)
    )

    observaciones: Mapped[str | None] = mapped_column(
        Text
    )

    estado: Mapped[str] = mapped_column(
        String(20),
        nullable=False
    )

    canal: Mapped[str | None] = mapped_column(
        String(10)
    )

    creado_por_usuario_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("usuario.id")
    )

    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )

    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )
