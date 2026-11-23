"""Controles de privacidad y separación de datos no confiables para IA."""

from __future__ import annotations

import json
import re


MAX_CAMPO_CLINICO = 1500
MAX_PROMPT_PROVEEDOR = 8000

_PATRONES_SENSIBLES = (
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), "[CORREO_PROTEGIDO]"),
    (re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{6,}\d)(?!\w)"), "[TELEFONO_PROTEGIDO]"),
    (re.compile(r"(?i)\b(?:ci|carnet|documento)\s*[:#-]?\s*[A-Z0-9-]{5,}\b"), "[IDENTIFICADOR_PROTEGIDO]"),
    (re.compile(r"https?://\S+", re.IGNORECASE), "[ENLACE_PROTEGIDO]"),
)

_INSTRUCCION_MALICIOSA = re.compile(
    r"(?i)(?:ignora|omite|revela|muestra|extrae|imprime|filtra)\s+"
    r"(?:todas?\s+)?(?:las?\s+)?(?:instrucciones?|prompts?|secretos?|datos?)"
)


def proteger_texto_clinico(valor: object) -> str:
    """Reduce texto libre, elimina controles y sustituye PII reconocible.

    Es una reducción de exposición, no una garantía de anonimización.
    """
    if valor is None:
        return "No proporcionado"
    texto = " ".join(str(valor).replace("\x00", " ").split())
    for patron, reemplazo in _PATRONES_SENSIBLES:
        texto = patron.sub(reemplazo, texto)
    return (texto[:MAX_CAMPO_CLINICO].strip() or "No proporcionado")


def validar_texto_usuario(texto: str) -> str:
    """Rechaza intentos explícitos de sustituir o extraer instrucciones."""
    limpio = " ".join(texto.replace("\x00", " ").split())
    if _INSTRUCCION_MALICIOSA.search(limpio):
        raise ValueError("El texto contiene instrucciones no permitidas")
    return proteger_texto_clinico(limpio)


def bloque_datos_no_confiables(campos: dict[str, object]) -> str:
    """Serializa solo una allowlist construida por el llamador, sin interpolación."""
    contenido = json.dumps(campos, ensure_ascii=False, sort_keys=True)
    if len(contenido) > MAX_PROMPT_PROVEEDOR:
        raise ValueError("Los datos exceden el límite permitido")
    return "<datos_no_confiables>\n" + contenido + "\n</datos_no_confiables>"
