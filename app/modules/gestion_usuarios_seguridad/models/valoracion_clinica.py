from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.modules.gestion_agenda_citas.models.models import Oftalmologo
from app.modules.gestion_historial_clinico.models.models import ConsultaClinica
from app.modules.gestion_pacientes.models.models import Paciente


class ValoracionClinica(Base):
    """CU40: Valoración registrada por un paciente sobre una atención médica.

    Única tabla nueva del CU40 (Opción A). Vive dentro de la base de datos de
    cada clínica (tenant) para garantizar aislamiento estricto entre empresas.
    Cada consulta clínica puede tener como máximo una valoración
    (`uq_valoracion_clinica_consulta`), que el paciente puede consultar y
    actualizar sin crear registros duplicados.

    Los avisos móviles post-atención reutilizan la tabla `public.notificacion`
    ya existente en los tenants, sin alteraciones estructurales.
    """

    __tablename__ = "valoracion_clinica"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    paciente_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("paciente.id", ondelete="RESTRICT"),
        nullable=False,
    )
    consulta_clinica_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("consulta_clinica.id", ondelete="RESTRICT"),
        nullable=False,
    )
    oftalmologo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("oftalmologo.id", ondelete="RESTRICT"),
        nullable=False,
    )
    puntuacion: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comentario: Mapped[str | None] = mapped_column(Text)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    paciente: Mapped[Paciente] = relationship()
    consulta_clinica: Mapped[ConsultaClinica] = relationship()
    oftalmologo: Mapped[Oftalmologo] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "consulta_clinica_id",
            name="uq_valoracion_clinica_consulta",
        ),
        CheckConstraint(
            "puntuacion >= 1 AND puntuacion <= 5",
            name="ck_valoracion_clinica_puntuacion",
        ),
        Index(
            "ix_valoracion_clinica_paciente_fecha",
            "paciente_id",
            "fecha_actualizacion",
        ),
        Index(
            "ix_valoracion_clinica_oftalmologo",
            "oftalmologo_id",
        ),
    )
