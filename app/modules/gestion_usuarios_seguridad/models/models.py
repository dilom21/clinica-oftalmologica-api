from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    correo: Mapped[str] = mapped_column(
        String(150),
        nullable=False
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )

    rol_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rol.id", ondelete="RESTRICT"),
        nullable=False
    )


class Rol(Base):
    __tablename__ = "rol"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    nombre: Mapped[str] = mapped_column(
        String(80),
        nullable=False
    )

    descripcion: Mapped[str | None] = mapped_column(
        String(255)
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )


class Modulo(Base):
    __tablename__ = "modulo"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    nombre: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    descripcion: Mapped[str | None] = mapped_column(
        String(255)
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )
    


class Funcion(Base):
    __tablename__ = "funcion"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )
    
    modulo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("modulo.id", ondelete="CASCADE"),
        nullable=False
    )

    nombre: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    descripcion: Mapped[str | None] = mapped_column(
        String(255)
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    __table_args__ = (
        UniqueConstraint(
            "modulo_id",
            "nombre",
            name="uq_funcion_modulo_nombre"
        ),
    )


class Accion(Base):
    __tablename__ = "accion"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    nombre: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    descripcion: Mapped[str | None] = mapped_column(
        String(255)
    )

    estado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )


class RolFuncion(Base):
    __tablename__ = "rol_funcion"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    rol_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rol.id", ondelete="CASCADE"),
        nullable=False
    )

    funcion_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("funcion.id", ondelete="CASCADE"),
        nullable=False
    )

    accion_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accion.id", ondelete="CASCADE"),
        nullable=False
    )

    descripcion: Mapped[str | None] = mapped_column(
        String(255)
    )

    __table_args__ = (
        UniqueConstraint(
            "rol_id",
            "funcion_id",
            "accion_id",
            name="uq_rol_funcion_accion"
        ),
    )


class TokenRecuperacion(Base):
    __tablename__ = "token_recuperacion"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    usuario_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("usuario.id", ondelete="CASCADE"),
        nullable=False
    )

    token_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )

    fecha_expiracion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )

    usado: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )


class Bitacora(Base):
    __tablename__ = "bitacora"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    usuario_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("usuario.id", ondelete="SET NULL")
    )

    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )

    ip: Mapped[str | None] = mapped_column(INET)

    accion: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    entidad_afectada: Mapped[str | None] = mapped_column(
        String(100)
    )

    id_registro_afectado: Mapped[int | None] = mapped_column(
        BigInteger
    )

    descripcion: Mapped[str | None] = mapped_column(Text)
