"""Local, bounded replies for social-only turns. Never authorizes operations."""
import re

from .privacidad import normalizar

_RELLENO = r"(?:ok(?:ay|ey)?|vale|perfecto|genial|listo|de acuerdo|entendido|muy bien|bueno)"
_SALUDO = r"(?:hola|buenas|buenos dias|buenas tardes|buenas noches|hey|saludos|que tal)"
_CIERRE = r"(?:eso es todo|nada mas|seria todo|ya esta|hasta luego|nos vemos|adios|chao|hasta pronto)"
_GRACIAS = r"(?:muchas |mil |miles de )?gracias"

_GRATITUD = re.compile(
    rf"^(?:{_SALUDO}[, ]*)?(?:{_RELLENO}[, ]*)?{_GRACIAS}"
    rf"(?:[, ]*{_CIERRE})?[.! ]*$")
_GRATITUD_CIERRE = re.compile(
    rf"^(?:{_SALUDO}[, ]*)?(?:{_RELLENO}[, ]*)?{_CIERRE}[, ]*{_GRACIAS}[.! ]*$")
_SOLO_SALUDO = re.compile(rf"^{_SALUDO}[.! ]*$")
_SOLO_CIERRE = re.compile(rf"^(?:{_RELLENO}[, ]*)?{_CIERRE}[.! ]*$")

DE_NADA = "¡De nada! Si necesitas otra consulta, aquí estoy."
DE_NADA_PENDIENTE = ("¡De nada! Recuerda que la propuesta sigue pendiente de confirmación "
                     "en el formulario de citas.")
SALUDO = "¡Hola! ¿En qué puedo ayudarte?"
DESPEDIDA = "¡Hasta luego! Si necesitas otra consulta, aquí estoy."


def respuesta_social(texto, *, propuesta_pendiente=False):
    """Devuelve una respuesta local solo para un turno social inequívoco.

    La coincidencia es sobre el mensaje completo: cualquier petición adicional
    («gracias, ¿qué horarios…?») sigue el flujo normal. No reserva ni confirma nada.
    """
    t = re.sub(r"\s+", " ", normalizar(texto)).strip().strip(" .,;:!¡?¿")
    if not t:
        return None
    if _GRATITUD.fullmatch(t) or _GRATITUD_CIERRE.fullmatch(t):
        return DE_NADA_PENDIENTE if propuesta_pendiente else DE_NADA
    if _SOLO_SALUDO.fullmatch(t):
        return SALUDO
    if _SOLO_CIERRE.fullmatch(t):
        return DESPEDIDA
    return None
