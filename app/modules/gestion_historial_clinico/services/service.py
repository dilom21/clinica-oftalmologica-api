from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_agenda_citas.repositories import repository as agenda_repo
from app.modules.gestion_historial_clinico.repositories import repository as repo
from app.modules.gestion_historial_clinico.schemas.schemas import (
    AntecedenteClinicoActualizar,
    AntecedenteClinicoCrear,
    ConsultaClinicaCrear,
    HistorialClinicoRespuesta,
)
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora


def consultar_historial_clinico(db: Session, paciente_id: int) -> HistorialClinicoRespuesta:
    paciente, historial = repo.obtener_paciente_con_historial(db, paciente_id)
    if paciente is None:
        raise HTTPException(status_code=404, detail="Paciente no encontrado")
    return HistorialClinicoRespuesta(paciente=paciente, historial=historial)


def crear_antecedente(db: Session, datos: AntecedenteClinicoCrear, usuario: Usuario):
    try:
        historial = repo.obtener_historial_activo_por_id(db, datos.historial_clinico_id)
        if historial is None:
            raise HTTPException(
                status_code=404, detail="Historial clínico no encontrado o inactivo",
            )

        antecedente = repo.crear_antecedente(db, datos)
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion="CREAR_ANTECEDENTE",
            entidad_afectada="antecedente_clinico",
            id_registro_afectado=antecedente.id,
            descripcion="Antecedente clínico registrado",
        )
        db.commit()
        db.refresh(antecedente)
        return antecedente
    except Exception:
        db.rollback()
        raise


def actualizar_antecedente(
    db: Session,
    antecedente_id: int,
    datos: AntecedenteClinicoActualizar,
    usuario: Usuario,
):
    try:
        antecedente = repo.obtener_antecedente_disponible_por_id(db, antecedente_id)
        if antecedente is None:
            raise HTTPException(
                status_code=404, detail="Antecedente clínico no encontrado o no disponible",
            )

        antecedente = repo.actualizar_antecedente(db, antecedente, datos)
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion="ACTUALIZAR_ANTECEDENTE",
            entidad_afectada="antecedente_clinico",
            id_registro_afectado=antecedente.id,
            descripcion="Antecedente clínico actualizado",
        )
        db.commit()
        db.refresh(antecedente)
        return antecedente
    except Exception:
        db.rollback()
        raise


# =========================================================
# CU15 - REGISTRAR CONSULTA CLÍNICA
# =========================================================

_ROL_OFTALMOLOGO = "oftalmologo"

BITACORA_REGISTRAR_CONSULTA = "REGISTRAR_CONSULTA_CLINICA"
ENTIDAD_CONSULTA_CLINICA = "consulta_clinica"

# Estados de cita admitidos por CU15 para iniciar una consulta.
# El catálogo global es ESTADOS_CITA_VALIDOS (agenda_citas.schemas); aquí solo
# se decide qué estados permiten abrir una atención: una cita CANCELADA o
# NO_ASISTIO no se atiende, y una cita ATENDIDA ya fue cerrada.
ESTADOS_CITA_INICIABLES_CU15 = (
    "PROGRAMADA",
    "CONFIRMADA",
    "EN_ESPERA",
)

ESTADO_CITA_ATENDIDA = "ATENDIDA"


def _es_violacion_unica_por_cita(exc: IntegrityError) -> bool:
    """Indica si el `IntegrityError` corresponde al UNIQUE de `cita_id`.

    La BD impone `uq_consulta_clinica_cita` sobre `consulta_clinica.cita_id`,
    así que ante una carrera entre dos registros simultáneos el INSERT falla en
    la base de datos y no en la validación previa. El mensaje del driver es la
    única evidencia disponible, y el motor puede ser PostgreSQL o SQLite en
    pruebas:

    - PostgreSQL: `duplicate key value violates unique constraint
      "uq_consulta_clinica_cita"`.
    - SQLite: `UNIQUE constraint failed: consulta_clinica.cita_id`.

    Cualquier otra violación de integridad (otra restricción, otra tabla, otra
    columna) devuelve `False` y el error original se relanza sin traducir: es
    preferible propagar un 500 honesto antes que atribuirle a este CU un
    duplicado que no ocurrió.
    """
    detalle = str(getattr(exc, "orig", None) or exc).lower().replace('"', "")

    # El nombre del índice/constraint es la evidencia más fuerte.
    if "uq_consulta_clinica_cita" in detalle:
        return True

    # SQLite y PostgreSQL cuando el mensaje nombra tabla y columna juntas.
    return "consulta_clinica" in detalle and "cita_id" in detalle


def registrar_consulta_clinica(
    db: Session,
    datos: ConsultaClinicaCrear,
    usuario: Usuario,
):
    """Registra una consulta clínica realizada por el oftalmólogo autenticado.

    El `oftalmologo_id` nunca llega desde el frontend: se resuelve a partir
    del usuario del token (usuario.id -> oftalmologo.usuario_id). Solo el rol
    Oftalmólogo puede registrar; se validan historial, paciente activo y cita
    (si se informa) incluyendo su estado y que no tenga ya una consulta
    asociada.

    Cuando la consulta se apoya en una cita, esa cita queda en ATENDIDA dentro
    de la misma unidad transaccional: consulta + estado de cita + bitácora se
    confirman con un único `db.commit()` o se descartan con un `db.rollback()`.
    """
    if nombre_rol_actual(usuario) != _ROL_OFTALMOLOGO:
        raise HTTPException(
            status_code=403,
            detail="Solo un oftalmólogo puede registrar consultas clínicas",
        )

    oftalmologo = agenda_repo.obtener_oftalmologo_activo_por_usuario_id(
        db, usuario.id,
    )
    if oftalmologo is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "El usuario autenticado no tiene un perfil de oftalmólogo "
                "activo"
            ),
        )

    historial = repo.obtener_historial_activo_por_id(
        db, datos.historial_clinico_id,
    )
    if historial is None:
        raise HTTPException(
            status_code=404,
            detail="Historial clínico no encontrado o inactivo",
        )

    if repo.obtener_paciente_activo_por_id(db, historial.paciente_id) is None:
        raise HTTPException(
            status_code=409,
            detail="El paciente asociado al historial clínico está inactivo",
        )

    cita = None
    if datos.cita_id is not None:
        cita = agenda_repo.obtener_cita_por_id(db, datos.cita_id)
        if cita is None:
            raise HTTPException(
                status_code=404, detail="Cita no encontrada",
            )
        if cita.oftalmologo_id != oftalmologo.id:
            raise HTTPException(
                status_code=403,
                detail="La cita no corresponde al oftalmólogo autenticado",
            )
        if cita.paciente_id != historial.paciente_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    "La cita no corresponde al paciente del historial "
                    "clínico"
                ),
            )
        if cita.estado not in ESTADOS_CITA_INICIABLES_CU15:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"No se puede registrar una consulta sobre una cita en "
                    f"estado {cita.estado}. Estados admitidos: "
                    f"{', '.join(ESTADOS_CITA_INICIABLES_CU15)}"
                ),
            )
        if repo.obtener_consulta_por_cita_id(db, datos.cita_id) is not None:
            raise HTTPException(
                status_code=409,
                detail="La cita ya tiene una consulta clínica registrada",
            )

    try:
        consulta = repo.crear_consulta_clinica(
            db,
            historial_clinico_id=historial.id,
            cita_id=datos.cita_id,
            oftalmologo_id=oftalmologo.id,
            motivo_consulta=datos.motivo_consulta,
            anamnesis=datos.anamnesis,
            observaciones=datos.observaciones,
        )

        if cita is not None:
            agenda_repo.actualizar_estado_cita(
                db, cita, ESTADO_CITA_ATENDIDA,
            )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_REGISTRAR_CONSULTA,
            entidad_afectada=ENTIDAD_CONSULTA_CLINICA,
            id_registro_afectado=consulta.id,
            descripcion="Consulta clínica registrada",
        )
        db.commit()
        db.refresh(consulta)
        return consulta
    except IntegrityError as exc:
        db.rollback()
        if _es_violacion_unica_por_cita(exc):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Ya existe una consulta clínica registrada para esta cita"
                ),
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise


def consultar_consulta_clinica(db: Session, consulta_id: int):
    consulta = repo.obtener_consulta_por_id(db, consulta_id)
    if consulta is None:
        raise HTTPException(
            status_code=404, detail="Consulta clínica no encontrada",
        )
    return consulta


def listar_consultas_clinicas(
    db: Session,
    *,
    historial_clinico_id: int | None = None,
    paciente_id: int | None = None,
    oftalmologo_id: int | None = None,
):
    return repo.listar_consultas_clinicas(
        db,
        historial_clinico_id=historial_clinico_id,
        paciente_id=paciente_id,
        oftalmologo_id=oftalmologo_id,
    )


# =========================================================
# CU16 - REGISTRAR DIAGNÓSTICO
# =========================================================

BITACORA_REGISTRAR_DIAGNOSTICO = "REGISTRAR_DIAGNOSTICO"
ENTIDAD_DIAGNOSTICO = "diagnostico"

_ROL_OFTALMOLOGO = "oftalmologo"


def registrar_diagnostico(
    db: Session,
    consulta_id: int,
    datos,
    usuario: Usuario,
):
    """Registra un diagnóstico para una consulta clínica del oftalmólogo autenticado.

    El oftalmólogo se resuelve desde el usuario autenticado (usuario.id -> oftalmologo.usuario_id).
    Solo el rol Oftalmólogo puede registrar; se valida que la consulta exista, esté activa
    y pertenezca al oftalmólogo autenticado.

    Diagnóstico + bitácora se confirman con un único commit o se descartan con rollback.
    """
    if nombre_rol_actual(usuario) != _ROL_OFTALMOLOGO:
        raise HTTPException(
            status_code=403,
            detail="Solo un oftalmólogo puede registrar diagnósticos",
        )

    oftalmologo = agenda_repo.obtener_oftalmologo_activo_por_usuario_id(
        db, usuario.id,
    )
    if oftalmologo is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "El usuario autenticado no tiene un perfil de oftalmólogo "
                "activo"
            ),
        )

    consulta = repo.obtener_consulta_activa_por_id(db, consulta_id)
    if consulta is None:
        raise HTTPException(
            status_code=404,
            detail="Consulta clínica no encontrada o inactiva",
        )

    if consulta.oftalmologo_id != oftalmologo.id:
        raise HTTPException(
            status_code=403,
            detail="La consulta no pertenece al oftalmólogo autenticado",
        )

    try:
        diagnostico = repo.crear_diagnostico(
            db,
            consulta_clinica_id=consulta.id,
            nombre=datos.nombre,
            descripcion=datos.descripcion,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_REGISTRAR_DIAGNOSTICO,
            entidad_afectada=ENTIDAD_DIAGNOSTICO,
            id_registro_afectado=diagnostico.id,
            descripcion="Diagnóstico registrado",
        )
        db.commit()
        db.refresh(diagnostico)
        return diagnostico
    except Exception:
        db.rollback()
        raise


def listar_diagnosticos(db: Session, consulta_id: int):
    """Lista los diagnósticos activos de una consulta clínica."""
    consulta = repo.obtener_consulta_activa_por_id(db, consulta_id)
    if consulta is None:
        raise HTTPException(
            status_code=404,
            detail="Consulta clínica no encontrada o inactiva",
        )
    return repo.listar_diagnosticos_por_consulta(db, consulta.id)
