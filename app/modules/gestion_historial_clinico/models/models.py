from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.modules.gestion_agenda_citas.models.models import Cita, Oftalmologo
from app.modules.gestion_pacientes.models.models import Paciente


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
    tratamientos: Mapped[list["Tratamiento"]] = relationship(
        back_populates="consulta_clinica",
        order_by=lambda: (Tratamiento.id.desc(),),
    )
    indicaciones: Mapped[list["Indicacion"]] = relationship(
        back_populates="consulta_clinica",
        order_by=lambda: (
            Indicacion.fecha_registro.desc(), Indicacion.id.desc(),
        ),
    )
    recetas: Mapped[list["Receta"]] = relationship(
        back_populates="consulta_clinica",
        order_by=lambda: (Receta.fecha_emision.desc(), Receta.id.desc()),
    )
    examenes: Mapped[list["ExamenOftalmologico"]] = relationship(
        back_populates="consulta_clinica",
        order_by=lambda: (
            ExamenOftalmologico.fecha_solicitud.desc(),
            ExamenOftalmologico.id.desc(),
        ),
    )


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


# =========================================================
# CU17 - REGISTRAR TRATAMIENTOS, INDICACIONES Y RECETAS
#
# Esquema alineado con el diagrama de clases oficial y con la BD real:
#   - tratamiento.observaciones  (antes se llamaba `indicaciones`)
#   - detalle_receta.presentacion
#   - indicacion (tabla propia, sin `estado`)
# Tratamiento, indicación y receta son independientes entre sí dentro de una
# misma consulta clínica y no requieren un diagnóstico previo.
# =========================================================


class Tratamiento(Base):
    __tablename__ = "tratamiento"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consulta_clinica_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("consulta_clinica.id", ondelete="CASCADE"),
        nullable=False,
    )
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    observaciones: Mapped[str | None] = mapped_column(Text)
    fecha_inicio: Mapped[date | None] = mapped_column(Date)
    fecha_fin: Mapped[date | None] = mapped_column(Date)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    consulta_clinica: Mapped[ConsultaClinica] = relationship(
        back_populates="tratamientos",
    )


class Indicacion(Base):
    __tablename__ = "indicacion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consulta_clinica_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("consulta_clinica.id", ondelete="CASCADE"),
        nullable=False,
    )
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    consulta_clinica: Mapped[ConsultaClinica] = relationship(
        back_populates="indicaciones",
    )


class Receta(Base):
    __tablename__ = "receta"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consulta_clinica_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("consulta_clinica.id", ondelete="CASCADE"),
        nullable=False,
    )
    fecha_emision: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    observaciones: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    consulta_clinica: Mapped[ConsultaClinica] = relationship(
        back_populates="recetas",
    )
    detalles: Mapped[list["DetalleReceta"]] = relationship(
        back_populates="receta",
        order_by=lambda: DetalleReceta.id.asc(),
    )


class DetalleReceta(Base):
    __tablename__ = "detalle_receta"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    receta_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("receta.id", ondelete="CASCADE"),
        nullable=False,
    )
    medicamento: Mapped[str] = mapped_column(String(150), nullable=False)
    presentacion: Mapped[str | None] = mapped_column(String(100))
    dosis: Mapped[str | None] = mapped_column(String(100))
    frecuencia: Mapped[str | None] = mapped_column(String(100))
    duracion: Mapped[str | None] = mapped_column(String(100))
    indicaciones: Mapped[str | None] = mapped_column(Text)

    receta: Mapped[Receta] = relationship(back_populates="detalles")


# =========================================================
# CU18 - REGISTRAR RESULTADOS DE EXAMENES OFTALMOLOGICOS
#
# Un examen pertenece a una consulta clinica y puede tener multiples
# resultados. `resultado_examen.examen_id` NO es unico deliberadamente.
# `archivo_url` solo almacena una referencia opcional; CU18 no gestiona
# archivos ni integra Supabase Storage.
# =========================================================


class ExamenOftalmologico(Base):
    __tablename__ = "examen_oftalmologico"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consulta_clinica_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("consulta_clinica.id", ondelete="CASCADE"),
        nullable=False,
    )
    nombre_examen: Mapped[str] = mapped_column(String(150), nullable=False)
    fecha_solicitud: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    observaciones: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    consulta_clinica: Mapped[ConsultaClinica] = relationship(
        back_populates="examenes",
    )
    resultados: Mapped[list["ResultadoExamen"]] = relationship(
        back_populates="examen",
        order_by=lambda: (
            ResultadoExamen.fecha_resultado.desc(),
            ResultadoExamen.id.desc(),
        ),
    )


class ResultadoExamen(Base):
    __tablename__ = "resultado_examen"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    examen_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("examen_oftalmologico.id", ondelete="CASCADE"),
        nullable=False,
    )
    fecha_resultado: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    resultado: Mapped[str] = mapped_column(Text, nullable=False)
    archivo_url: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    examen: Mapped[ExamenOftalmologico] = relationship(
        back_populates="resultados",
    )
