"""CU37 - Emitir constancia de cita médica.

Multitenant: la sesión `db` ya apunta a la base de datos de la clínica del
usuario autenticado (resuelta desde el JWT en `get_db`). La constancia se
genera al vuelo desde `cita`, `paciente` y `oftalmologo` del tenant actual;
el nombre de la clínica sale del contexto del tenant. No hay tablas nuevas
ni datos propios de una clínica concreta.
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.repositories import repository as repo
from app.modules.gestion_agenda_citas.services import citas
from app.modules.gestion_agenda_citas.services.constancia_pdf import (
    DatosConstancia,
    generar_constancia_pdf,
    numero_constancia,
)
from app.modules.gestion_pacientes.repositories.repository import (
    obtener_paciente_por_id,
)
from app.modules.gestion_pagos.services.service import (
    _nombre_clinica_comprobante,
)
from app.modules.gestion_usuarios_seguridad.repositories.repository import (
    registrar_bitacora,
)

BITACORA_EMITIR_CONSTANCIA_CITA = "EMITIR_CONSTANCIA_CITA"
ESTADO_CITA_ATENDIDA = "ATENDIDA"


def generar_constancia_cita(
    db: Session,
    cita_id: int,
    usuario=None,
) -> tuple[bytes, str]:
    """Devuelve (pdf, nombre_archivo) de la constancia de una cita ATENDIDA.

    Un paciente solo puede obtener la constancia de sus propias citas
    (`citas.obtener_cita` responde 404 si la cita no es suya).
    """
    cita = citas.obtener_cita(db, cita_id, usuario=usuario)

    if cita.estado != ESTADO_CITA_ATENDIDA:
        raise HTTPException(
            status_code=409,
            detail="Solo se emite constancia de citas en estado ATENDIDA",
        )

    paciente = obtener_paciente_por_id(db, cita.paciente_id)
    oftalmologo = repo.obtener_oftalmologo_por_id(db, cita.oftalmologo_id)
    if paciente is None or oftalmologo is None:
        raise HTTPException(
            status_code=404,
            detail="No se encontraron los datos de la cita",
        )

    datos = DatosConstancia(
        clinica_nombre=_nombre_clinica_comprobante(db),
        cita_id=cita.id,
        fecha_emision=datetime.now(timezone.utc),
        paciente_nombres=paciente.nombres,
        paciente_apellidos=paciente.apellidos,
        paciente_ci=paciente.ci,
        oftalmologo_nombres=oftalmologo.nombres,
        oftalmologo_apellidos=oftalmologo.apellidos,
        oftalmologo_matricula=oftalmologo.matricula,
        oftalmologo_especialidad=oftalmologo.especialidad,
        fecha_cita=cita.fecha,
        hora_inicio=cita.hora_inicio,
        hora_fin=cita.hora_fin,
        motivo=cita.motivo,
        estado=cita.estado,
    )
    contenido = generar_constancia_pdf(datos)

    try:
        registrar_bitacora(
            db=db,
            usuario_id=getattr(usuario, "id", None),
            accion=BITACORA_EMITIR_CONSTANCIA_CITA,
            entidad_afectada=citas.ENTIDAD_CITA,
            id_registro_afectado=cita.id,
            descripcion=(
                f"Constancia {numero_constancia(cita.id)} emitida"
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return contenido, f"constancia-cita-{numero_constancia(cita.id)}.pdf"
