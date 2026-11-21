"""Small, public-only dialogue state. Never appointment records or private IDs."""
from datetime import date, time
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .privacidad import normalizar, respuesta_local


class Referencia(BaseModel):
    id: int = Field(gt=0)
    nombre: str = Field(max_length=200)


class ContextoPublico(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intencion: str | None = Field(default=None, max_length=40)
    oftalmologo_id: int | None = None
    profesional: str | None = Field(default=None, max_length=200)
    fecha: date | None = None
    preferencia: Literal["manana", "tarde"] | None = None
    hora: time | None = None
    propuesta_preparada: bool = False
    profesionales: list[Referencia] = Field(default_factory=list, max_length=10)
    servicios: list[Referencia] = Field(default_factory=list, max_length=10)
    busqueda_servicios: str = Field(default="", max_length=150)
    siguiente_offset: int = Field(default=0, ge=0, le=1010)
    mas_servicios: bool | None = None
    siguiente_profesional: int = Field(default=0,ge=0,le=1010)
    mas_profesionales: bool | None = None


def intencion_clara(texto):
    """Hints, not authorization or a phrase table; unknown language still goes to LLM."""
    t = normalizar(texto)
    medicos=bool(re.search(r"\boftalmolog(?:os?|as?)\b|especialistas?|profesionales|doctores|medicos|facultativ\w*|oculistas",t))
    if re.search(r"\b(citas?|turnos?)\b", t) and re.search(r"\b(mi|mis|tengo|proxim\w*|siguiente)\b", t):
        return "consultar_mis_citas"
    if (re.search(r"disponibilidad|horarios?|huecos?|turnos? libres", t) or
        (re.search(r"disponib",t) and re.search(r"lunes|martes|miercoles|jueves|viernes|sabado|domingo|\d{4}-\d{2}-\d{2}|fecha",t))) and not re.search(r"horario.*(?:general|clinica|apertura)", t):
        return "consultar_disponibilidad"
    if medicos and re.search(r"lunes|martes|miercoles|jueves|viernes|sabado|domingo|por la (?:manana|tarde)|\d{4}-\d{2}-\d{2}|\d{1,2} de [a-z]+",t):
        return "consultar_disponibilidad"
    if medicos and re.search(r"\b(?:ese|esa|mismo|misma)\b",t): return None
    if medicos or (re.search(r"\bquien(?:es)?\b",t) and re.search(r"atiend|trabaj",t)):
        return "consultar_profesionales"
    if re.search(r"servicios?|catalogo|precios?|tarifas|cuesta|ofrecen|atenciones", t):
        return "consultar_servicios"
    if re.search(r"direccion|telefono|correo|contacto|horario.*(?:general|clinica|apertura)|informacion.*clinica|nombre.*clinica", t):
        return "consultar_clinica"
    return None


_LISTADO_SERVICIOS = re.compile(
    r"\b(?:que|cuales|cuantos)\s+(?:son\s+)?(?:los\s+|las\s+)?(?:servicios?|atenciones|precios?|tarifas)\b"
    r"|\b(?:catalogo|lista(?:do)?\s+de\s+servicios|todos\s+los\s+servicios|todas\s+las\s+atenciones)\b"
    r"|\bprecios\s+de\s+(?:las?\s+)?(?:atenciones|servicios|consultas)\b"
    r"|\bservicios?\s+(?:oftalmologicos?|disponibles|de\s+la\s+clinica|que\s+(?:ofrece|ofrecen|tienen|hay)|ofrecidos|activos)\b"
    r"|\b(?:muestra(?:me)?|ensena(?:me)?|ver|conocer|dime|decir|indica(?:r)?|mostrar)\s+(?:me\s+)?(?:el|los|tu)\s+(?:catalogo|servicios)\b"
    r"|\binformacion\s+(?:sobre|de)\s+(?:los\s+)?servicios\b"
    r"|\bcuanto\s+cuestan\s+(?:los\s+|las\s+)?(?:servicios|atenciones|consultas)\b"
)
# Solo se usa para impedir que un listado general absorba una búsqueda concreta.
_FILTRO_SERVICIO = re.compile(
    r"\b(?:busco|buscando|buscar|detalle|llamad[oa]|denominad[oa]|especific[oa]|en\s+particular|solo|solamente)\b"
    r"|\bcuanto\s+(?:cuesta|sale|vale)\b"
    r"|\bservicio\s+(?:numero|nro\.?|id|#)\s*\d"
    r"|\b(?:el|la)\s+(?:primer[oa]|segund[oa]|tercer[oa]|cuart[oa]|quint[oa])\b"
    r"|\b(?:ese|esa|este|esta|aquel|aquella)\s+servicio\b"
    r"|\bservicio\s+(?:de|del)\s+(?!la\s+clinica|esta\s+clinica|las?\s+atenciones)\w+"
)


def listado_general_servicios(texto):
    """True solo para una petición inequívoca de catálogo completo.

    Las búsquedas por nombre, precio, detalle, referencias y paginación siguen
    el camino semántico/contextual; no se convierten en listados completos.
    """
    t = normalizar(texto)
    return bool(_LISTADO_SERVICIOS.search(t)) and not bool(_FILTRO_SERVICIO.search(t))


def preferencia(texto):
    t = normalizar(texto)
    if re.search(r"(?:por|en|de) la manana|matutin|solo.*manana", t): return "manana"
    if re.search(r"(?:por|en|de) la tarde|vespertin|solo.*tarde", t): return "tarde"
    return None


def hora_elegida(texto, franja):
    t = normalizar(texto)
    palabras = {"una":1,"dos":2,"tres":3,"cuatro":4,"cinco":5,"seis":6,
                "siete":7,"ocho":8,"nueve":9,"diez":10,"once":11,"doce":12}
    m = re.search(r"\b(?:a la[s]?|el de la[s]?|horario de la[s]?)\s+(\d{1,2}|" + "|".join(palabras) + r")(?:[:.](\d{2})|\s+y\s+(media|cuarto))?\b", t)
    if not m: return None
    if re.match(r"\s*(?:y\b|[:.]\d|de la noche)",t[m.end():]): return None
    h = palabras.get(m[1], int(m[1]) if m[1].isdigit() else -1)
    minutos = int(m[2]) if m[2] else (30 if m[3]=="media" else 15 if m[3]=="cuarto" else 0)
    if h < 12 and franja=="tarde" and (not m[2] or preferencia(texto)=="tarde"): h += 12
    if franja is None and not m[2]: return None  # ask about ambiguous spoken hours
    if not (0 <= h <= 23 and 0 <= minutos <= 59): return None
    return time(h, minutos)


def preparar_contexto(anterior, texto):
    c = anterior.model_copy(deep=True)
    # Explicit private references stay local: never send appointment IDs to LLM.
    referencia = re.search(r"\bcita\s+(?:numero|nro\.?|id|#)\s*(\d{1,19})\b",normalizar(texto))
    if referencia:
        destino=("reprogramar_cita" if "reprogram" in normalizar(texto) else
                 "cancelar_cita" if "cancel" in normalizar(texto) else "detalle_cita")
        return ContextoPublico(intencion="orientar_navegacion"),("orientar_navegacion",{"destino":destino,"cita_id":int(referencia[1])})
    hint = intencion_clara(texto)
    general_servicios = listado_general_servicios(texto)
    if general_servicios:
        c = ContextoPublico(intencion="consultar_servicios")
    elif hint == "consultar_servicios":
        # Un tema nuevo de servicios no conserva disponibilidad, pero sí las
        # referencias ya mostradas para precios, detalle y paginación.
        c = ContextoPublico(intencion=hint, servicios=c.servicios,
                            busqueda_servicios=c.busqueda_servicios,
                            siguiente_offset=c.siguiente_offset,
                            mas_servicios=c.mas_servicios)
    elif hint in {"consultar_clinica", "consultar_mis_citas"}:
        c = ContextoPublico(intencion=hint)
    if hint=="consultar_profesionales":
        c.intencion=hint; c.oftalmologo_id=None; c.profesional=None; c.hora=None; c.propuesta_preparada=False
    franja = preferencia(texto)
    # Corrections involving dates/new names belong to semantic interpretation,
    # not to a stale deterministic continuation.
    correccion = bool(re.search(r"\d{4}-\d{2}-\d{2}|lunes|martes|miercoles|jueves|viernes|sabado|domingo|otra fecha|cambia.*fecha", normalizar(texto)))
    coincidencias = [r for r in c.profesionales if normalizar(r.nombre) in normalizar(texto)]
    ordinal=re.search(r"\b(?:el|la) (primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|quint[oa])\b",normalizar(texto))
    if not coincidencias and ordinal and not re.search(r"paciente|servicio|cita",normalizar(texto)):
        pos=next((i for i,raiz in enumerate(("primer","segund","tercer","cuart","quint")) if ordinal[1].startswith(raiz)),None)
        if pos is not None and pos<len(c.profesionales): coincidencias=[c.profesionales[pos]]
    if c.intencion=="consultar_servicios" and c.mas_servicios and re.search(r"continua|siguientes|mas opciones|otros servicios",normalizar(texto)):
        return c,("consultar_servicios",{"busqueda":c.busqueda_servicios,"offset":min(c.siguiente_offset,1000),"limite":5})
    if c.intencion=="consultar_profesionales" and c.mas_profesionales and re.search(r"continua|siguientes|mas opciones|otros profesionales",normalizar(texto)):
        return c,("consultar_profesionales",{"offset":min(c.siguiente_profesional,1000),"limite":5})
    t = normalizar(texto)
    servicio_ref = [r for r in c.servicios if r.nombre and normalizar(r.nombre) in t]
    ordinal_servicio = None if servicio_ref else re.search(
        r"\b(?:el|la) (primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|quint[oa])\b", t)
    if (ordinal_servicio and re.search(r"servicio|catalogo|precio|cuesta|detalle", t)
            and not re.search(r"profesional|doctor|medico|oftalmolog", t)):
        pos = next((i for i, raiz in enumerate(("primer", "segund", "tercer", "cuart", "quint"))
                    if ordinal_servicio[1].startswith(raiz)), None)
        if pos is not None and pos < len(c.servicios):
            servicio_ref = [c.servicios[pos]]
    if len(servicio_ref) == 1 and re.search(
            r"precio|cuesta|cuanto|detalle|informacion|servicio|ese|esa|mismo|misma", t):
        return c, ("consultar_detalle_servicio", {"servicio_id": servicio_ref[0].id})
    if not coincidencias and re.search(r"cambia.*profesional|otr[oa] (?:profesional|doctor|especialista)|\bcon (?:el |la )?(?:dr\.?|doctor|doctora)\b",normalizar(texto)):
        c.oftalmologo_id=None; c.profesional=None; c.hora=None; c.propuesta_preparada=False
    if len(coincidencias)==1:
        r=coincidencias[0]
        if r.id != c.oftalmologo_id: c.hora=None; c.propuesta_preparada=False
        c.oftalmologo_id=r.id; c.profesional=r.nombre
    if franja: c.preferencia=franja; c.hora=None; c.propuesta_preparada=False
    hora=hora_elegida(texto,c.preferencia)
    if hora: c.hora=hora
    if c.oftalmologo_id and c.fecha and not correccion and c.intencion in {"consultar_disponibilidad","preparar_solicitud_cita","consultar_profesionales"}:
        if hora:
            return c, ("preparar_solicitud_cita", {"oftalmologo_id":c.oftalmologo_id,"fecha":c.fecha.isoformat(),"hora_inicio":hora.isoformat()})
        if franja or coincidencias:
            return c, ("consultar_disponibilidad", {"oftalmologo_id":c.oftalmologo_id,"fecha":c.fecha.isoformat()})
    return c, None


def registrar(c, tarjeta):
    if tarjeta.privacidad=="privada":
        return ContextoPublico(intencion="consultar_mis_citas" if tarjeta.tipo=="citas_propias" else "orientar_navegacion")
    c=c.model_copy(deep=True)
    if tarjeta.tipo=="servicios":
        c.intencion="consultar_servicios"
        refs={r.id:r for r in c.servicios}
        refs.update({r.id:Referencia(id=r.id,nombre=r.nombre[:200]) for r in tarjeta.servicios if not respuesta_local(r.nombre)})
        c.servicios=list(refs.values())[:10]
        if tarjeta.offset is not None:
            c.busqueda_servicios=tarjeta.busqueda or ""
            c.siguiente_offset=tarjeta.offset+len(tarjeta.servicios)
            c.mas_servicios=tarjeta.hay_mas
    if tarjeta.tipo=="profesionales":
        c.profesionales=[Referencia(id=r.id,nombre=r.nombre[:200]) for r in tarjeta.profesionales if not respuesta_local(r.nombre)][:10]
        if c.intencion not in {"consultar_disponibilidad","preparar_solicitud_cita"}: c.intencion="consultar_profesionales"
        if tarjeta.offset is not None:
            c.siguiente_profesional=tarjeta.offset+len(tarjeta.profesionales)
            c.mas_profesionales=tarjeta.hay_mas
    if tarjeta.tipo in {"disponibilidad","propuesta_cita"}:
        if (c.oftalmologo_id,c.fecha)!=(tarjeta.oftalmologo_id,tarjeta.fecha):
            c.hora=None; c.propuesta_preparada=False
        c.oftalmologo_id=tarjeta.oftalmologo_id; c.fecha=tarjeta.fecha
        c.profesional=tarjeta.profesional.nombre if tarjeta.profesional else next((r.nombre for r in c.profesionales if r.id==c.oftalmologo_id),None)
        c.intencion="consultar_disponibilidad" if tarjeta.tipo=="disponibilidad" else "preparar_solicitud_cita"
        if tarjeta.tipo=="propuesta_cita": c.hora=tarjeta.hora_inicio; c.propuesta_preparada=True
    return c
