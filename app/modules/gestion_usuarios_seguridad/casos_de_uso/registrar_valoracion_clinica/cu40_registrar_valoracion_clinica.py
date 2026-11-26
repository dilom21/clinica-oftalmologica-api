"""CU40 - Registrar y Actualizar valoración de la clínica (Móvil).

Punto de entrada del caso de uso dentro de
`gestion_usuarios_seguridad/casos_de_uso/registrar_valoracion_clinica/`.

Diseño simplificado (Opción A):
- `models/valoracion_clinica.py`: única tabla nueva (`ValoracionClinica`).
- `schemas/valoracion_clinica.py`: DTOs Pydantic y validaciones de escala 1..5.
- `repositories/valoracion_clinica.py`: acceso a datos sin `commit()`,
  reutilizando la tabla `public.notificacion` existente en cada tenant.
- `services/valoracion_clinica_service.py`: reglas de negocio, aislamiento
  multitenant, prevención de duplicados, avisos post-atención y bitácora.
- `api/valoracion_clinica.py`: endpoints FastAPI integrados en `api/router.py`.
"""

from app.modules.gestion_usuarios_seguridad.api.valoracion_clinica import (
    actualizar_valoracion_clinica,
    consultar_mi_valoracion_por_consulta,
    guardar_o_actualizar_valoracion_por_consulta,
    listar_mis_valoraciones,
    listar_notificaciones_valoracion_pendientes,
    marcar_notificacion_valoracion_leida,
    obtener_resumen_valoraciones,
    registrar_valoracion_clinica,
    router,
)
from app.modules.gestion_usuarios_seguridad.models.valoracion_clinica import (
    ValoracionClinica,
)
from app.modules.gestion_usuarios_seguridad.schemas.valoracion_clinica import (
    AtencionValorableRespuesta,
    ESCALA_VALORACION,
    NotificacionValoracionRespuesta,
    OftalmologoValoracionResumen,
    PUNTUACION_MAXIMA,
    PUNTUACION_MINIMA,
    ResumenValoracionesPacienteRespuesta,
    ValoracionClinicaActualizar,
    ValoracionClinicaCrear,
    ValoracionClinicaRespuesta,
)
from app.modules.gestion_usuarios_seguridad.services.valoracion_clinica_service import (
    actualizar_valoracion,
    consultar_valoracion_por_consulta,
    disparar_aviso_valoracion_post_atencion,
    guardar_o_actualizar_valoracion_por_consulta as ejecutar_guardar_o_actualizar,
    listar_mis_valoraciones as ejecutar_listar_mis_valoraciones,
    listar_notificaciones_pendientes,
    marcar_notificacion_leida,
    obtener_resumen_valoraciones as ejecutar_obtener_resumen,
    registrar_valoracion,
)

__all__ = [
    "AtencionValorableRespuesta",
    "ESCALA_VALORACION",
    "NotificacionValoracionRespuesta",
    "OftalmologoValoracionResumen",
    "PUNTUACION_MAXIMA",
    "PUNTUACION_MINIMA",
    "ResumenValoracionesPacienteRespuesta",
    "ValoracionClinica",
    "ValoracionClinicaActualizar",
    "ValoracionClinicaCrear",
    "ValoracionClinicaRespuesta",
    "actualizar_valoracion",
    "actualizar_valoracion_clinica",
    "consultar_mi_valoracion_por_consulta",
    "consultar_valoracion_por_consulta",
    "disparar_aviso_valoracion_post_atencion",
    "ejecutar_guardar_o_actualizar",
    "ejecutar_listar_mis_valoraciones",
    "ejecutar_obtener_resumen",
    "guardar_o_actualizar_valoracion_por_consulta",
    "listar_mis_valoraciones",
    "listar_notificaciones_pendientes",
    "listar_notificaciones_valoracion_pendientes",
    "marcar_notificacion_leida",
    "marcar_notificacion_valoracion_leida",
    "obtener_resumen_valoraciones",
    "registrar_valoracion",
    "registrar_valoracion_clinica",
    "router",
]
