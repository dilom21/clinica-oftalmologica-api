from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from app.database.base import Base

class NotificacionPaciente(Base):
    __tablename__ = "notificacion_paciente"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), Identity(always=True), primary_key=True)
    paciente_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("paciente.id", ondelete="CASCADE"), nullable=False)
    titulo: Mapped[str] = mapped_column(String(150), nullable=False)
    mensaje: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    fecha_envio: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leida: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    fecha_lectura: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clave_evento: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        UniqueConstraint("paciente_id", "clave_evento", name="uq_notificacion_paciente_evento"),
        Index("ix_notificacion_paciente_lista", "paciente_id", fecha_creacion.desc(), id.desc()),
    )
