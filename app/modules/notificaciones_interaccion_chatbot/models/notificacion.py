from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Notificacion(Base):
    __tablename__ = "notificacion"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), Identity(always=True), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False)
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    evento_clave: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    titulo: Mapped[str] = mapped_column(String(150), nullable=False)
    cuerpo: Mapped[str] = mapped_column(Text, nullable=False)
    datos: Mapped[dict | None] = mapped_column(JSON)
    cita_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cita.id", ondelete="SET NULL"))
    leida: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    fecha_lectura: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    @property
    def mensaje(self) -> str:
        return self.cuerpo

    @property
    def fecha_envio(self) -> None:
        return None
