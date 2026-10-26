"""CU25 - El paciente consulta únicamente su información clínica vigente."""

from collections import defaultdict

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_historial_clinico.repositories import (
    informacion_autorizada as repo,
)
from app.modules.gestion_historial_clinico.schemas.informacion_autorizada import (
    AntecedenteAutorizadoRespuesta,
    ConsultaAutorizadaRespuesta,
    ControlAutorizadoRespuesta,
    DiagnosticoAutorizadoRespuesta,
    ExamenAutorizadoRespuesta,
    IndicacionAutorizadaRespuesta,
    InformacionClinicaAutorizadaRespuesta,
    OftalmologoClinicoResumen,
    RecetaAutorizadaRespuesta,
    TratamientoAutorizadoRespuesta,
)
from app.modules.gestion_pacientes.repositories.repository import (
    obtener_paciente_por_usuario_id,
)
from app.modules.gestion_usuarios_seguridad.models.models import Usuario


def consultar_informacion_clinica_autorizada(
    db: Session,
    usuario: Usuario,
) -> InformacionClinicaAutorizadaRespuesta:
    """Arma la información clínica que este paciente está autorizado a ver.

    El paciente se deduce del token. No se acepta un `paciente_id` del cliente,
    así que un paciente no puede pedir el historial de otro.
    """
    if nombre_rol_actual(usuario) != "paciente":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Solo un paciente puede consultar su información clínica "
                "autorizada"
            ),
        )

    paciente = obtener_paciente_por_usuario_id(db, usuario.id)
    if paciente is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El usuario no tiene un perfil de paciente",
        )
    if not paciente.estado:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El perfil de paciente está inactivo",
        )

    historial, consultas, diagnosticos, controles = (
        repo.obtener_informacion_clinica_autorizada(db, paciente.id)
    )
    if historial is None:
        return InformacionClinicaAutorizadaRespuesta(
            historial_id=None,
            fecha_apertura=None,
            observaciones_generales=None,
            antecedentes=[],
            consultas=[],
            controles=[],
        )

    diagnosticos_por_consulta: dict[int, list] = defaultdict(list)
    for diagnostico in diagnosticos:
        diagnosticos_por_consulta[diagnostico.consulta_clinica_id].append(
            diagnostico
        )

    consultas_respuesta = []
    for consulta in consultas:
        if consulta.oftalmologo is None:
            continue
        consultas_respuesta.append(
            ConsultaAutorizadaRespuesta(
                id=consulta.id,
                fecha_consulta=consulta.fecha_consulta,
                motivo_consulta=consulta.motivo_consulta,
                anamnesis=consulta.anamnesis,
                observaciones=consulta.observaciones,
                oftalmologo=OftalmologoClinicoResumen.model_validate(
                    consulta.oftalmologo
                ),
                diagnosticos=[
                    DiagnosticoAutorizadoRespuesta.model_validate(item)
                    for item in diagnosticos_por_consulta[consulta.id]
                ],
                tratamientos=[
                    TratamientoAutorizadoRespuesta.model_validate(item)
                    for item in consulta.tratamientos
                ],
                indicaciones=[
                    IndicacionAutorizadaRespuesta.model_validate(item)
                    for item in consulta.indicaciones
                ],
                recetas=[
                    RecetaAutorizadaRespuesta.model_validate(item)
                    for item in consulta.recetas
                ],
                examenes=[
                    ExamenAutorizadoRespuesta.model_validate(item)
                    for item in consulta.examenes
                ],
            )
        )

    controles_respuesta = [
        ControlAutorizadoRespuesta(
            id=control.id,
            fecha_programada=control.fecha_programada,
            motivo=control.motivo,
            observaciones=control.observaciones,
            estado=control.estado,
            oftalmologo=OftalmologoClinicoResumen.model_validate(oftalmologo),
        )
        for control, oftalmologo in controles
    ]

    return InformacionClinicaAutorizadaRespuesta(
        historial_id=historial.id,
        fecha_apertura=historial.fecha_apertura,
        observaciones_generales=historial.observaciones_generales,
        antecedentes=[
            AntecedenteAutorizadoRespuesta.model_validate(item)
            for item in repo.antecedentes_autorizados(historial)
        ],
        consultas=consultas_respuesta,
        controles=controles_respuesta,
    )
