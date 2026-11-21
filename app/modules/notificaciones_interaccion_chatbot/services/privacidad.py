"""Conservative rejection, NOT guaranteed anonymization. Never log the input."""
import re
import unicodedata
from datetime import date


def normalizar(texto):
    return "".join(c for c in unicodedata.normalize("NFKD", texto.lower())
                   if not unicodedata.combining(c))


SECRETOS = re.compile(
    r"eyJ[\w-]+\.[\w-]+\.[\w-]+|(?:sk-|postgres(?:ql)?://|bearer\s|password|"
    r"contrasena|api.?key|cadena de conexion|database_name|tenant_id)|"
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|\d[\d ()+.-]{6,}\d|"
    r"\b(?:mi nombre|me llamo|soy [a-z]+ [a-z]+|mi direccion|mi cedula|mi carnet|mi ci\b|mi dni|mi documento|mi identificacion|"
    r"mi telefono|mi correo|mi cuenta bancaria|mi tarjeta)\b", re.I,
)
MEDICO = re.compile(
    r"\b(?:tengo(?! (?:una? )?cita)|siento|me duele|me arde|veo borroso|dolor|gotas|dosis|medicar|mi diagnostico|mi examen|"
    r"receta|diagnostico|resultados? (?:de|del)|historia clinica|mis resultados|mi historial|anamnesis|prescrib|medicamento|"
    r"tratamiento personalizado|interpretar.*examen)\b", re.I,
)
INYECCION = re.compile(
    r"ignora.*instruccion|instrucciones anteriores|system prompt|<\|.*\|>|"
    r"\b(?:sql|select|drop table|exec|eval|configuracion interna)\b|"
    r"otra clinica|otro tenant|otros pacientes|otro paciente|cambia.*(?:tenant|empresa)|"
    r"(?:registra|reserva|cancela|modifica|elimina).*(?:sin confirm|todas|precio)", re.I,
)


def respuesta_local(texto):
    valor = normalizar(texto)
    # Calendar dates are legitimate scheduling data, not telephone numbers.
    def calendario(match):
        try:
            date.fromisoformat(match.group())
            return "fecha"
        except ValueError:
            return match.group()
    valor = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", calendario, valor)
    if re.search(r"perdi.*vision|perdida.*vision|no (?:puedo )?ver|golpe.*ojo|dolor.*intenso|quemadura.*ojo", valor):
        return "Busca atención médica urgente. No esperes una conversación administrativa para recibir atención."
    if SECRETOS.search(valor) or MEDICO.search(valor):
        return "Por tu privacidad, no compartas datos personales ni información médica aquí. Para consultas médicas acude a un profesional. Puedo orientarte sobre servicios y citas."
    if INYECCION.search(valor):
        return "Solo puedo orientar sobre servicios y citas de tu clínica actual. No accedo a datos clínicos, credenciales ni ejecuto cambios."
    return None


def salida_segura(texto, clave=""):
    return (isinstance(texto, str) and 0 < len(texto.strip()) <= 3000
            and not respuesta_local(texto)
            and not (clave and clave in texto)
            and not re.search(r"https?://|\d|(?:he |ya )(?:reservado|cancelado|registrado|reprogramado)|"
                              r"cita (?:confirmada|reservada|registrada)|gratuit|gratis|bolivianos|dolares|"
                              r"\b(?:cuesta|cuestan|costo|coste|precio|tarifa|importe|monto|abrimos|cerramos|atendemos|"
                              r"reservad[oa]|registrad[oa]|cancelad[oa]|reprogramad[oa]|confirmad[oa])\b|"
                              r"(?:diagnostico|tomar.*mg)", normalizar(texto)))
