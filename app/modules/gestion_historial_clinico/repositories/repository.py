from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_historial_clinico.models.models import (
    AntecedenteClinico,
    ConsultaClinica,
    DetalleReceta,
    Diagnostico,
    ExamenOftalmologico,
    HistorialClinico,
    Indicacion,
    Receta,
    ResultadoExamen,
    Tratamiento,
)
from app.modules.gestion_historial_clinico.schemas.schemas import (
    AntecedenteClinicoActualizar,
    AntecedenteClinicoCrear,
)
from app.modules.gestion_pacientes.repositories.repository import obtener_paciente_por_id


def obtener_paciente_con_historial(db: Session, paciente_id: int):
    paciente = obtener_paciente_por_id(db, paciente_id)
    if paciente is None:
        return None, None

    historial = db.scalar(
        select(HistorialClinico)
        .options(
            selectinload(
                HistorialClinico.antecedentes.and_(AntecedenteClinico.estado.is_(True))
            )
        )
        .where(
            HistorialClinico.paciente_id == paciente_id,
            HistorialClinico.estado.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    return paciente, historial


def obtener_historial_activo_por_id(db: Session, historial_clinico_id: int):
    return db.scalar(
        select(HistorialClinico).where(
            HistorialClinico.id == historial_clinico_id,
            HistorialClinico.estado.is_(True),
        )
    )


# =========================================================
# HISTORIAL CLÍNICO AUTOMÁTICO (CU07 / REGISTRO MÓVIL)
# Todo paciente registrado debe poseer exactamente un historial.
# Estas funciones NO confirman la transacción: la capa de servicio decide
# cuándo hacer commit/rollback para que paciente + historial + bitácora
# pertenezcan a la misma transacción.
# =========================================================


def obtener_historial_por_paciente_id(db: Session, paciente_id: int):
    """Historial del paciente incluyendo el inactivo (o `None`).

    A diferencia de `obtener_paciente_con_historial` (CU13, que solo expone
    historiales activos), aquí sí se devuelve el historial inactivo para no
    intentar crear un duplicado: `historial_clinico.paciente_id` es UNIQUE.
    """
    return db.scalar(
        select(HistorialClinico).where(
            HistorialClinico.paciente_id == paciente_id,
        )
    )


def crear_historial_clinico(
    db: Session,
    *,
    paciente_id: int,
    fecha_apertura: datetime,
    observaciones_generales: str | None = None,
    estado: bool = True,
):
    """Crea el historial clínico del paciente sin hacer commit.

    `fecha_apertura` se recibe desde el servicio para mantenerla coherente con
    `paciente.fecha_registro`; nunca se inventan observaciones.
    """
    historial = HistorialClinico(
        paciente_id=paciente_id,
        fecha_apertura=fecha_apertura,
        observaciones_generales=observaciones_generales,
        estado=estado,
    )
    db.add(historial)
    db.flush()
    db.refresh(historial)
    return historial


def asegurar_historial_clinico_para_paciente(db: Session, paciente):
    """Devuelve el historial del paciente creándolo solo si realmente no existe.

    Operación idempotente pensada para CU07 y para el registro móvil:
    - si el paciente ya posee historial (activo o inactivo) se reutiliza igual;
    - nunca crea duplicados;
    - no reactiva historiales inactivos;
    - no ejecuta commit (la transacción pertenece a la capa de servicio).
    """
    historial = obtener_historial_por_paciente_id(db, paciente.id)
    if historial is not None:
        return historial

    return crear_historial_clinico(
        db,
        paciente_id=paciente.id,
        fecha_apertura=paciente.fecha_registro,
    )


def obtener_paciente_activo_por_id(db: Session, paciente_id: int):
    """Paciente existente y activo (o `None`).

    Reutiliza `obtener_paciente_por_id` del módulo de pacientes para no
    duplicar la resolución del registro.
    """
    paciente = obtener_paciente_por_id(db, paciente_id)
    if paciente is None or not paciente.estado:
        return None
    return paciente


def obtener_antecedente_disponible_por_id(db: Session, antecedente_id: int):
    return db.scalar(
        select(AntecedenteClinico)
        .join(HistorialClinico, HistorialClinico.id == AntecedenteClinico.historial_clinico_id)
        .where(
            AntecedenteClinico.id == antecedente_id,
            AntecedenteClinico.estado.is_(True),
            HistorialClinico.estado.is_(True),
        )
    )


def crear_antecedente(db: Session, datos: AntecedenteClinicoCrear):
    antecedente = AntecedenteClinico(
        historial_clinico_id=datos.historial_clinico_id,
        tipo=datos.tipo,
        descripcion=datos.descripcion,
        fecha_registro=datetime.now(timezone.utc),
        estado=True,
    )
    db.add(antecedente)
    db.flush()
    db.refresh(antecedente)
    return antecedente


def actualizar_antecedente(
    db: Session,
    antecedente: AntecedenteClinico,
    datos: AntecedenteClinicoActualizar,
):
    antecedente.tipo = datos.tipo
    antecedente.descripcion = datos.descripcion
    db.flush()
    db.refresh(antecedente)
    return antecedente


# =========================================================
# CU15 - REGISTRAR CONSULTA CLÍNICA
# =========================================================


def crear_consulta_clinica(
    db: Session,
    *,
    historial_clinico_id: int,
    cita_id: int | None,
    oftalmologo_id: int,
    motivo_consulta: str | None,
    anamnesis: str | None,
    observaciones: str | None,
):
    consulta = ConsultaClinica(
        historial_clinico_id=historial_clinico_id,
        cita_id=cita_id,
        oftalmologo_id=oftalmologo_id,
        fecha_consulta=datetime.now(timezone.utc),
        motivo_consulta=motivo_consulta,
        anamnesis=anamnesis,
        observaciones=observaciones,
        estado=True,
    )
    db.add(consulta)
    db.flush()
    db.refresh(consulta)
    return consulta


def obtener_consulta_por_id(db: Session, consulta_id: int):
    return db.scalar(
        select(ConsultaClinica)
        .options(selectinload(ConsultaClinica.oftalmologo))
        .where(ConsultaClinica.id == consulta_id)
    )


def obtener_consulta_por_cita_id(db: Session, cita_id: int):
    return db.scalar(
        select(ConsultaClinica).where(ConsultaClinica.cita_id == cita_id)
    )


def listar_consultas_clinicas(
    db: Session,
    *,
    historial_clinico_id: int | None = None,
    paciente_id: int | None = None,
    oftalmologo_id: int | None = None,
):
    stmt = select(ConsultaClinica).options(
        selectinload(ConsultaClinica.oftalmologo)
    )

    if paciente_id is not None:
        stmt = stmt.join(
            HistorialClinico,
            HistorialClinico.id == ConsultaClinica.historial_clinico_id,
        ).where(HistorialClinico.paciente_id == paciente_id)

    if historial_clinico_id is not None:
        stmt = stmt.where(
            ConsultaClinica.historial_clinico_id == historial_clinico_id
        )

    if oftalmologo_id is not None:
        stmt = stmt.where(ConsultaClinica.oftalmologo_id == oftalmologo_id)

    stmt = stmt.order_by(
        ConsultaClinica.fecha_consulta.desc(),
        ConsultaClinica.id.desc(),
    )

    return db.scalars(stmt).all()


# =========================================================
# CU16 - REGISTRAR DIAGNÓSTICO
# =========================================================


def obtener_consulta_activa_por_id(db: Session, consulta_id: int):
    return db.scalar(
        select(ConsultaClinica)
        .options(selectinload(ConsultaClinica.oftalmologo))
        .where(
            ConsultaClinica.id == consulta_id,
            ConsultaClinica.estado.is_(True),
        )
    )


def crear_diagnostico(
    db: Session,
    *,
    consulta_clinica_id: int,
    nombre: str,
    descripcion: str | None,
):
    diagnostico = Diagnostico(
        consulta_clinica_id=consulta_clinica_id,
        nombre=nombre,
        descripcion=descripcion,
        fecha_diagnostico=datetime.now(timezone.utc),
        estado=True,
    )
    db.add(diagnostico)
    db.flush()
    db.refresh(diagnostico)
    return diagnostico


def listar_diagnosticos_por_consulta(db: Session, consulta_clinica_id: int):
    stmt = (
        select(Diagnostico)
        .where(
            Diagnostico.consulta_clinica_id == consulta_clinica_id,
            Diagnostico.estado.is_(True),
        )
        .order_by(
            Diagnostico.fecha_diagnostico.desc(),
            Diagnostico.id.desc(),
        )
    )
    return db.scalars(stmt).all()


# =========================================================
# CU17 - REGISTRAR TRATAMIENTOS, INDICACIONES Y RECETAS
#
# Ninguna funcion confirma la transaccion: el servicio es el dueno del
# commit/rollback (una receta con sus detalles y su bitacora = una unidad).
# =========================================================


def crear_tratamiento(
    db: Session,
    *,
    consulta_clinica_id: int,
    descripcion: str,
    observaciones: str | None,
    fecha_inicio: date | None,
    fecha_fin: date | None,
):
    tratamiento = Tratamiento(
        consulta_clinica_id=consulta_clinica_id,
        descripcion=descripcion,
        observaciones=observaciones,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        estado=True,
    )
    db.add(tratamiento)
    db.flush()
    db.refresh(tratamiento)
    return tratamiento


def listar_tratamientos_por_consulta(db: Session, consulta_clinica_id: int):
    stmt = (
        select(Tratamiento)
        .where(
            Tratamiento.consulta_clinica_id == consulta_clinica_id,
            Tratamiento.estado.is_(True),
        )
        # fecha_inicio es opcional (NULL): el id da un orden descendente
        # estable (lo mas reciente primero) sin depender de los NULL.
        .order_by(Tratamiento.id.desc())
    )
    return db.scalars(stmt).all()


def crear_indicacion(
    db: Session,
    *,
    consulta_clinica_id: int,
    descripcion: str,
):
    indicacion = Indicacion(
        consulta_clinica_id=consulta_clinica_id,
        descripcion=descripcion,
        fecha_registro=datetime.now(timezone.utc),
    )
    db.add(indicacion)
    db.flush()
    db.refresh(indicacion)
    return indicacion


def listar_indicaciones_por_consulta(db: Session, consulta_clinica_id: int):
    stmt = (
        select(Indicacion)
        .where(Indicacion.consulta_clinica_id == consulta_clinica_id)
        .order_by(
            Indicacion.fecha_registro.desc(),
            Indicacion.id.desc(),
        )
    )
    return db.scalars(stmt).all()


def crear_receta(
    db: Session,
    *,
    consulta_clinica_id: int,
    observaciones: str | None,
):
    receta = Receta(
        consulta_clinica_id=consulta_clinica_id,
        fecha_emision=datetime.now(timezone.utc),
        observaciones=observaciones,
        estado=True,
    )
    db.add(receta)
    db.flush()
    db.refresh(receta)
    return receta


def crear_detalle_receta(
    db: Session,
    *,
    receta_id: int,
    medicamento: str,
    presentacion: str | None = None,
    dosis: str | None = None,
    frecuencia: str | None = None,
    duracion: str | None = None,
    indicaciones: str | None = None,
):
    detalle = DetalleReceta(
        receta_id=receta_id,
        medicamento=medicamento,
        presentacion=presentacion,
        dosis=dosis,
        frecuencia=frecuencia,
        duracion=duracion,
        indicaciones=indicaciones,
    )
    db.add(detalle)
    db.flush()
    db.refresh(detalle)
    return detalle


def listar_recetas_por_consulta(db: Session, consulta_clinica_id: int):
    stmt = (
        select(Receta)
        # selectinload evita el N+1 al serializar los detalles anidados.
        .options(selectinload(Receta.detalles))
        .where(
            Receta.consulta_clinica_id == consulta_clinica_id,
            Receta.estado.is_(True),
        )
        .order_by(
            Receta.fecha_emision.desc(),
            Receta.id.desc(),
        )
    )
    return db.scalars(stmt).all()


def obtener_receta_por_id(db: Session, receta_id: int):
    return db.scalar(
        select(Receta)
        .options(selectinload(Receta.detalles))
        .where(Receta.id == receta_id)
    )


# =========================================================
# CU18 - REGISTRAR RESULTADOS DE EXAMENES OFTALMOLOGICOS
#
# Igual que en CU17, el repositorio solo agrega/consulta/flush/refresh. La
# transaccion completa (registro clinico + bitacora) pertenece al Service.
# =========================================================


def crear_examen(
    db: Session,
    *,
    consulta_clinica_id: int,
    nombre_examen: str,
    observaciones: str | None,
):
    examen = ExamenOftalmologico(
        consulta_clinica_id=consulta_clinica_id,
        nombre_examen=nombre_examen,
        fecha_solicitud=datetime.now(timezone.utc),
        observaciones=observaciones,
        estado=True,
    )
    db.add(examen)
    db.flush()
    db.refresh(examen)
    return examen


def listar_examenes_por_consulta(db: Session, consulta_clinica_id: int):
    stmt = (
        select(ExamenOftalmologico)
        .options(
            selectinload(
                ExamenOftalmologico.resultados.and_(
                    ResultadoExamen.estado.is_(True)
                )
            )
        )
        .where(
            ExamenOftalmologico.consulta_clinica_id == consulta_clinica_id,
            ExamenOftalmologico.estado.is_(True),
        )
        .order_by(
            ExamenOftalmologico.fecha_solicitud.desc(),
            ExamenOftalmologico.id.desc(),
        )
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).all()


def obtener_examen_activo_por_id(db: Session, examen_id: int):
    return db.scalar(
        select(ExamenOftalmologico)
        .options(
            selectinload(ExamenOftalmologico.consulta_clinica),
            selectinload(
                ExamenOftalmologico.resultados.and_(
                    ResultadoExamen.estado.is_(True)
                )
            ),
        )
        .where(
            ExamenOftalmologico.id == examen_id,
            ExamenOftalmologico.estado.is_(True),
        )
        .execution_options(populate_existing=True)
    )


def crear_resultado_examen(
    db: Session,
    *,
    examen_id: int,
    resultado: str,
    archivo_url: str | None,
):
    resultado_examen = ResultadoExamen(
        examen_id=examen_id,
        fecha_resultado=datetime.now(timezone.utc),
        resultado=resultado,
        archivo_url=archivo_url,
        estado=True,
    )
    db.add(resultado_examen)
    db.flush()
    db.refresh(resultado_examen)
    return resultado_examen


def listar_resultados_por_examen(db: Session, examen_id: int):
    stmt = (
        select(ResultadoExamen)
        .where(
            ResultadoExamen.examen_id == examen_id,
            ResultadoExamen.estado.is_(True),
        )
        .order_by(
            ResultadoExamen.fecha_resultado.desc(),
            ResultadoExamen.id.desc(),
        )
    )
    return db.scalars(stmt).all()
