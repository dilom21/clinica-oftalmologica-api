"""Servicio de asistencia clínica IA.

Orquesta el flujo de cada endpoint:

    Router -> IA Service -> (repository existente) -> consulta clínica
                         -> DeepSeek Provider
                         -> validación Pydantic
                         -> registrar_bitacora + commit

La IA es solo asistencia: nunca modifica la consulta clínica ni crea o
actualiza diagnósticos. La única escritura del flujo exitoso es la bitácora.
Los prompts enviados al modelo incluyen exclusivamente datos clínicos del
caso (motivo, anamnesis, observaciones) y jamás datos personales del paciente.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.core.time import fecha_local_aplicacion
from app.modules.gestion_agenda_citas.repositories import repository as agenda_repo
from app.modules.gestion_historial_clinico.repositories import (
    repository as historial_repo,
)
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.repositories.repository import (
    registrar_bitacora,
)
from app.modules.gestion_reportes.registry.datasets import DATASETS
from app.modules.gestion_reportes.services.service import validate as validate_report
from app.modules.integracion_ia.providers import deepseek_provider
from app.modules.integracion_ia.providers.deepseek_provider import (
    IAConfiguracionError,
    IAProveedorNoDisponibleError,
    IARespuestaInvalidaError,
)
from app.modules.integracion_ia.schemas import schemas


_ROL_OFTALMOLOGO = "oftalmologo"

BITACORA_ANALIZAR_CONSULTA = "ANALIZAR_CONSULTA_IA"
BITACORA_MEJORAR_REDACCION = "MEJORAR_REDACCION_DIAGNOSTICO_IA"
ENTIDAD_CONSULTA_CLINICA = "consulta_clinica"

DESCRIPCION_BITACORA_ANALIZAR = (
    "Análisis asistido por IA solicitado sobre consulta clínica"
)
DESCRIPCION_BITACORA_MEJORAR = (
    "Redacción diagnóstica asistida por IA generada"
)
BITACORA_INTERPRETAR_REPORTE = "INTERPRETAR_REPORTE_IA"
ENTIDAD_REPORTE = "reporte"
DESCRIPCION_BITACORA_REPORTE = (
    "Interpretación de consulta de reporte mediante IA"
)

# =========================================================
# PROMPTS
# =========================================================

SYSTEM_ANALISIS = (
    "Eres un asistente de documentación y apoyo clínico para un oftalmólogo.\n"
    "No sustituyes el criterio profesional.\n"
    "No confirmes diagnósticos.\n"
    "No prescribas medicamentos ni tratamientos.\n"
    "No inventes datos.\n"
    "Si la información es insuficiente, indícalo.\n"
    "Devuelve exclusivamente json válido con la estructura solicitada."
)

SYSTEM_MEJORA_REDACCION = (
    "Eres un asistente de redacción clínica.\n"
    "Debes mejorar claridad, gramática y terminología de la descripción "
    "proporcionada.\n"
    "No cambies el diagnóstico nombrado por el profesional.\n"
    "No agregues información clínica que no exista en el texto original.\n"
    "No diagnostiques.\n"
    "No prescribas.\n"
    "Devuelve exclusivamente json válido."
)

_TEXTO_AUSENTE = "No proporcionado"


def _texto_clinico(valor) -> str:
    """Normaliza un campo clínico opcional para el prompt, sin PII."""
    if valor is None:
        return _TEXTO_AUSENTE
    texto = str(valor).strip()
    return texto or _TEXTO_AUSENTE


def _construir_prompt_analisis(consulta) -> str:
    """User prompt del análisis: solo motivo, anamnesis y observaciones."""
    return (
        "Analiza la siguiente consulta clínica de oftalmología y devuelve "
        "exclusivamente un objeto JSON con la estructura indicada.\n\n"
        f"motivo_consulta: {_texto_clinico(consulta.motivo_consulta)}\n"
        f"anamnesis: {_texto_clinico(consulta.anamnesis)}\n"
        f"observaciones: {_texto_clinico(consulta.observaciones)}\n\n"
        "Estructura JSON esperada:\n"
        "{\n"
        '  "resumen_clinico": "texto",\n'
        '  "hallazgos_relevantes": ["texto"],\n'
        '  "aspectos_a_evaluar": ["texto"],\n'
        '  "hipotesis_orientativas": ["texto"],\n'
        '  "advertencia": "Contenido generado por IA para apoyo profesional. '
        'Debe ser validado por el oftalmólogo."\n'
        "}\n"
    )


def _construir_prompt_mejora(nombre: str, descripcion: str) -> str:
    """User prompt de la mejora: conserva el nombre y solo reescribe."""
    return (
        "Mejora la redacción de la siguiente descripción diagnóstica sin "
        "cambiar el diagnóstico indicado por el profesional y sin agregar "
        "información clínica nueva.\n\n"
        f"Diagnóstico (no modificar): {nombre}\n"
        f"Descripción original: {descripcion}\n\n"
        "Devuelve exclusivamente un objeto JSON con la estructura:\n"
        "{\n"
        '  "descripcion_mejorada": "texto"\n'
        "}\n"
    )


# =========================================================
# SEGURIDAD COMÚN (patrón CU15/CU16)
# =========================================================


def _resolver_consulta_autorizada(db: Session, consulta_id: int, usuario: Usuario):
    """Valida rol Oftalmólogo, perfil activo y ownership de la consulta.

    Reutiliza exactamente el patrón probado en CU15/CU16: el oftalmólogo se
    deriva del usuario autenticado y nunca se acepta desde el cliente.
    """
    if nombre_rol_actual(usuario) != _ROL_OFTALMOLOGO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un oftalmólogo puede usar la asistencia clínica IA",
        )

    oftalmologo = agenda_repo.obtener_oftalmologo_activo_por_usuario_id(
        db, usuario.id,
    )
    if oftalmologo is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "El usuario autenticado no tiene un perfil de oftalmólogo "
                "activo"
            ),
        )

    consulta = historial_repo.obtener_consulta_activa_por_id(db, consulta_id)
    if consulta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consulta clínica no encontrada o inactiva",
        )

    if consulta.oftalmologo_id != oftalmologo.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La consulta no pertenece al oftalmólogo autenticado",
        )

    return consulta


# =========================================================
# PROVEEDOR
# =========================================================


def _solicitar_json(system_prompt: str, user_prompt: str) -> dict:
    """Invoca al provider y traduce sus errores a HTTP controlados."""
    try:
        return deepseek_provider.generar_json(system_prompt, user_prompt)
    except IAConfiguracionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de IA no configurado",
        ) from exc
    except IAProveedorNoDisponibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de IA no disponible",
        ) from exc
    except IARespuestaInvalidaError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Respuesta inválida del servicio de IA",
        ) from exc


SYSTEM_INTERPRETAR_REPORTE = (
    "Eres un traductor de lenguaje natural a configuración de reportes.\n"
    "No generes SQL ni ejecutes reportes.\n"
    "No inventes datasets, campos ni operadores: usa únicamente el catálogo.\n"
    "Las únicas propiedades JSON permitidas son exactamente: dataset, columnas, "
    "filtros, orden, limit, requiere_aclaracion, pregunta_aclaracion, "
    "accion_sugerida y formato_sugerido. No uses labels ni agregues propiedades.\n"
    "Copia exactamente las keys del catálogo para dataset y campos; no uses sus labels.\n"
    "Los booleanos deben ser booleanos JSON true o false, no texto.\n"
    "Usa solo operadores permitidos por campo y como máximo 3 órdenes.\n"
    "El límite máximo es 200. Devuelve exclusivamente JSON válido.\n"
    "accion_sugerida solo puede ser previsualizar o exportar.\n"
    "Si accion_sugerida es previsualizar, formato_sugerido debe ser null.\n"
    "Si accion_sugerida es exportar, formato_sugerido solo puede ser xlsx, pdf, csv o html.\n"
    "Si la intención es ambigua, marca requiere_aclaracion=true.\n"
    "No necesitas datos reales de pacientes ni debes inventar registros.\n"
    "Solo transforma la intención del usuario en una configuración lógica."
)


def _catalogo_reportes_seguro() -> list[dict]:
    """Construye el catálogo únicamente desde el registry, sin consultar BD."""
    return [
        {
            "key": dataset_key,
            "label": dataset["label"],
            "campos": [
                {
                    "key": field_key,
                    "label": field.label,
                    "tipo": field.tipo,
                    "operadores": list(field.operadores),
                }
                for field_key, field in dataset["fields"].items()
            ],
            "ordenables": list(dataset["fields"]),
        }
        for dataset_key, dataset in DATASETS.items()
    ]


def _construir_prompt_reporte(texto: str) -> str:
    catalogo = json.dumps(
        _catalogo_reportes_seguro(), ensure_ascii=False, sort_keys=True,
    )
    return (
        "Interpreta el siguiente comando ya transcrito. No uses datos reales.\n"
        f"Fecha actual: {fecha_local_aplicacion().isoformat()}\n"
        f"Catálogo permitido: {catalogo}\n"
        f"Texto del usuario: {texto}\n\n"
        "Devuelve exactamente un objeto JSON con estas únicas keys y ningún campo "
        "adicional: dataset, columnas, filtros, orden, limit, "
        "requiere_aclaracion, pregunta_aclaracion, accion_sugerida y "
        "formato_sugerido. Copia las keys del catálogo exactamente, no sus labels. "
        "Usa true/false como booleanos JSON. Valores exactos: accion_sugerida es "
        "previsualizar o exportar; formato_sugerido es null si es previsualizar, "
        "o xlsx/pdf/csv/html si es exportar."
    )


def _normalizar_interpretacion_reporte(datos: dict) -> dict:
    """Corrige únicamente dos variantes inequívocas observadas del proveedor.

    La copia evita mutar la respuesta original. Todo valor no reconocido queda
    intacto para que Pydantic lo rechace; no se transforman datasets ni campos.
    """
    normalizado = dict(datos)
    if normalizado.get("accion_sugerida") == "listar":
        normalizado["accion_sugerida"] = "previsualizar"
    if normalizado.get("formato_sugerido") == "tabla":
        normalizado["formato_sugerido"] = None
    return normalizado


def _validar_interpretacion_reporte(datos: dict) -> schemas.InterpretacionReporteIARespuesta:
    """Aplica JSON, normalización mínima, Pydantic y registry, en ese orden."""
    try:
        contenido_json = json.loads(
            datos if isinstance(datos, str) else json.dumps(datos)
        )
        if not isinstance(contenido_json, dict):
            raise TypeError("La respuesta no es un objeto JSON")
        contenido_json = _normalizar_interpretacion_reporte(contenido_json)
        contenido = schemas.InterpretacionReporteIARespuesta.model_validate(
            contenido_json
        )
        validate_report(
            contenido.dataset,
            contenido.columnas,
            contenido.filtros,
            contenido.orden,
        )
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError, HTTPException) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Respuesta inválida del servicio de IA",
        ) from exc
    return contenido


def interpretar_reporte(
    db: Session,
    datos: schemas.InterpretarReporteIARequest,
    usuario: Usuario,
) -> schemas.InterpretacionReporteIARespuesta:
    """Interpreta una orden sin ejecutar consultas, SQL ni exportaciones."""
    datos_ia = _solicitar_json(
        SYSTEM_INTERPRETAR_REPORTE,
        _construir_prompt_reporte(datos.texto),
    )
    contenido = _validar_interpretacion_reporte(datos_ia)

    try:
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_INTERPRETAR_REPORTE,
            entidad_afectada=ENTIDAD_REPORTE,
            descripcion=DESCRIPCION_BITACORA_REPORTE,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return contenido


def _registrar_bitacora_ia(
    db: Session,
    usuario: Usuario,
    accion: str,
    consulta_id: int,
    descripcion: str,
) -> None:
    """Registra la bitácora de la operación IA exitosa en una transacción.

    No incluye prompt, respuesta del modelo, datos clínicos ni API key.
    """
    try:
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=accion,
            entidad_afectada=ENTIDAD_CONSULTA_CLINICA,
            id_registro_afectado=consulta_id,
            descripcion=descripcion,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


# =========================================================
# ENDPOINT A - ANALIZAR CONSULTA
# =========================================================


def analizar_consulta(
    db: Session,
    consulta_id: int,
    usuario: Usuario,
) -> schemas.AnalisisConsultaIARespuesta:
    """Analiza una consulta clínica del oftalmólogo autenticado con DeepSeek."""
    consulta = _resolver_consulta_autorizada(db, consulta_id, usuario)

    datos_ia = _solicitar_json(
        SYSTEM_ANALISIS,
        _construir_prompt_analisis(consulta),
    )

    try:
        contenido = schemas.AnalisisConsultaIARespuesta.model_validate(datos_ia)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Respuesta inválida del servicio de IA",
        ) from exc

    # La advertencia de uso profesional la impone el backend, no el modelo.
    contenido.advertencia = schemas.ADVERTENCIA_ANALISIS

    _registrar_bitacora_ia(
        db,
        usuario,
        BITACORA_ANALIZAR_CONSULTA,
        consulta.id,
        DESCRIPCION_BITACORA_ANALIZAR,
    )

    return contenido


# =========================================================
# ENDPOINT B - MEJORAR REDACCIÓN DE DIAGNÓSTICO
# =========================================================


def mejorar_redaccion_diagnostico(
    db: Session,
    consulta_id: int,
    datos: schemas.MejorarRedaccionDiagnosticoIARequest,
    usuario: Usuario,
) -> schemas.MejorarRedaccionDiagnosticoIARespuesta:
    """Mejora solo la redacción de la descripción diagnóstica indicada.

    El `consulta_id` de la URL se usa para validar ownership antes de llamar
    al proveedor. No se crea ni actualiza ningún diagnóstico en la BD: el
    oftalmólogo decide luego si usa el borrador mediante CU16.
    """
    consulta = _resolver_consulta_autorizada(db, consulta_id, usuario)

    datos_ia = _solicitar_json(
        SYSTEM_MEJORA_REDACCION,
        _construir_prompt_mejora(datos.nombre, datos.descripcion),
    )

    try:
        contenido = schemas.MejorarRedaccionDiagnosticoIAContenido.model_validate(
            datos_ia,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Respuesta inválida del servicio de IA",
        ) from exc

    respuesta = schemas.MejorarRedaccionDiagnosticoIARespuesta(
        nombre=datos.nombre,
        descripcion_original=datos.descripcion,
        descripcion_mejorada=contenido.descripcion_mejorada,
        advertencia=schemas.ADVERTENCIA_MEJORA,
    )

    _registrar_bitacora_ia(
        db,
        usuario,
        BITACORA_MEJORAR_REDACCION,
        consulta.id,
        DESCRIPCION_BITACORA_MEJORAR,
    )

    return respuesta
