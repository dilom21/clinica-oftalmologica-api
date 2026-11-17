from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.modules.gestion_historial_clinico.models.models import ConsultaClinica


class ControlMedico(Base):
    """Mapeo de la tabla existente, incluidos sus vínculos históricos."""

    __tablename__ = "control_medico"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consulta_clinica_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("consulta_clinica.id", ondelete="SET NULL"),
    )
    paciente_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("paciente.id", ondelete="RESTRICT"), nullable=False,
    )
    oftalmologo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("oftalmologo.id", ondelete="RESTRICT"), nullable=False,
    )
    fecha_programada: Mapped[date] = mapped_column(Date, nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(255))
    observaciones: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str | None] = mapped_column(String(30), default="PROGRAMADO")

    consulta_clinica: Mapped[ConsultaClinica | None] = relationship()
