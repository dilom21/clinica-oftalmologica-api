"""Authoritative local narration of typed results, never LLM-authored critical facts."""
import re
import html
from datetime import time

from .privacidad import normalizar, respuesta_local

# Existing financial domain: PlanPago CK moneda='BOB' and CU34 supports BOB.
# Catalog has no per-service currency field; do not infer it from Stripe config.
MONEDA_CATALOGO = "bolivianos"
MESES = ("enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre")


def para_voz(texto):
    t=re.sub(r"```[^\n]*\n(.*?)```",r"\1",texto,flags=re.S)
    t=re.sub(r"!?\[([^\]]+)\]\([^)]*\)",r"\1",t)
    t=re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)", "", t)
    t=re.sub(r"(\*\*|__|~~|`)","",t)
    t=re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)",r"\1",t)
    t=html.unescape(re.sub(r"<[^>]+>","",t))
    return re.sub(r"\s+"," ",t).strip()


def fecha_hablada(d): return f"{d.day} de {MESES[d.month-1]} de {d.year}"


def hora_hablada(h):
    return f"{h.hour}" + (f":{h.minute:02d}" if h.minute else "")


def _nombre(s):
    # A malicious catalog string is data, but not suitable for speaking instructions.
    return s if not respuesta_local(s) else "Servicio del catálogo"


def deduplicar(tarjetas):
    resultado=[]
    servicios={}; profesionales={}
    posicion_servicios=None; posicion_profesionales=None
    vistos=set()
    for t in tarjetas:
        if t.tipo in {"servicios","profesionales"}:
            datos=servicios if t.tipo=="servicios" else profesionales
            if t.tipo=="servicios" and posicion_servicios is None:
                posicion_servicios=len(resultado); resultado.append(t.model_copy(update={"servicios":[]}))
            if t.tipo=="profesionales" and posicion_profesionales is None:
                posicion_profesionales=len(resultado); resultado.append(t.model_copy(update={"profesionales":[]}))
            for row in getattr(t,t.tipo):
                if row.id not in datos: datos[row.id]=row
                else:
                    # Retain valid fields; a later null must not erase a known Decimal.
                    old=datos[row.id]
                    datos[row.id]=old.model_copy(update={k:v for k,v in row.model_dump().items() if v is not None})
        else:
            key=t.model_dump_json()
            if key not in vistos: vistos.add(key); resultado.append(t)
    for pos,datos,campo in ((posicion_servicios,servicios,"servicios"),(posicion_profesionales,profesionales,"profesionales")):
        if pos is not None:
            rows=list(datos.values()); resultado[pos]=resultado[pos].model_copy(update={campo:rows[:10]})
            estados=[t.hay_mas for t in tarjetas if t.tipo==campo]
            resultado[pos]=resultado[pos].model_copy(update={"hay_mas":True if True in estados else False if all(x is False for x in estados) else None})
            # Preserve complementary entities beyond the existing per-card contract.
            for i in range(10,len(rows),10): resultado.append(resultado[pos].model_copy(update={campo:rows[i:i+10]}))
    return resultado


def componer(tarjetas, mensaje, contexto, incidencias=()):
    escritos=[]; hablados=[]
    consulta=normalizar(mensaje)
    for t in tarjetas:
        if t.tipo=="servicios":
            if not t.servicios:
                if t.offset:
                    texto=("No hay más resultados para esa búsqueda." if t.busqueda
                           else "No hay más resultados en esa página del catálogo.")
                elif t.busqueda:
                    texto="No encontré servicios que coincidan con esa búsqueda."
                else:
                    texto="No encontré servicios activos en el catálogo de esta clínica."
                escritos.append(texto); hablados.append(texto); continue
            lineas=[]
            for s in t.servicios:
                numero=f"{s.precio:.2f}" if s.precio is not None else None
                precio=((numero[:-3] if numero.endswith(".00") else numero.replace(".",","))+f" {MONEDA_CATALOGO}" if numero is not None else "precio no registrado")
                duracion=f"; duración de {s.duracion_minutos} minutos" if s.duracion_minutos is not None else "; duración no registrada"
                lineas.append(f"{_nombre(s.nombre)}: {precio}{duracion}.")
            escritos.extend(lineas)
            hablados.extend(lineas[:3])
            if len(lineas)>3 or t.hay_mas: hablados.append("Hay más opciones en esta consulta. ¿Quieres que continúe?")
            if t.hay_mas: escritos.append("Hay más resultados; puedo continuar con la siguiente página.")
        elif t.tipo=="profesionales":
            if not t.profesionales: texto="No encontré profesionales activos en la consulta realizada."
            else: texto="Encontré a " + "; ".join(f"{_nombre(p.nombre)}"+(f", especialista en {p.especialidad}" if p.especialidad and not respuesta_local(p.especialidad) else "") for p in t.profesionales) + "."
            escritos.append(texto)
            hablados.append(texto if len(t.profesionales)<=3 else "Encontré a "+"; ".join(_nombre(p.nombre) for p in t.profesionales[:3])+". Hay más profesionales; puedo continuar.")
            if t.hay_mas: escritos.append("La lista continúa en la siguiente página.")
        elif t.tipo=="clinica":
            campos={"nombre":"La clínica", "direccion":"Dirección", "telefono":"Teléfono", "correo":"Correo"}
            aliases={"nombre":r"nombre|se llama", "direccion":r"direccion|ubicacion|\bdonde\b|domicilio",
                     "telefono":r"telefono|contacto|llamar", "correo":r"correo|email|escribir"}
            pedidos=[k for k in campos if re.search(aliases[k],consulta)]
            if not pedidos and not re.search(r"horario|apertura|abre|cierr",consulta): pedidos=list(campos)
            partes=[f"{campos[k]}: {getattr(t,k) if getattr(t,k) else 'dato no registrado'}." for k in pedidos]
            if re.search(r"horario|apertura|abre|cierr",consulta): partes.append("El horario general de atención no está registrado.")
            texto=" ".join(partes) or "No hay información institucional registrada para esa consulta."
            escritos.append(texto); hablados.append(texto)
        elif t.tipo=="disponibilidad":
            franja=(" por la mañana" if t.franja=="manana" else " por la tarde" if t.franja=="tarde" else "")
            nombre=f" con {_nombre(t.profesional.nombre)}" if t.profesional else ""
            if not t.intervalos: texto=f"No encontré horarios disponibles{franja}{nombre} para el {fecha_hablada(t.fecha)}. Podemos consultar otra fecha."
            else:
                horas=", ".join(hora_hablada(i.hora_inicio) for i in t.intervalos)
                texto=f"Para el {fecha_hablada(t.fecha)}{nombre} encontré {len(t.intervalos)} horarios{franja}: {horas}. ¿Qué hora prefieres?"
            escritos.append(texto)
            if len(t.intervalos)>5:
                hablados.append(f"Para el {fecha_hablada(t.fecha)}{nombre} hay {len(t.intervalos)} horarios{franja}, con inicios entre las {hora_hablada(t.intervalos[0].hora_inicio)} y las {hora_hablada(t.intervalos[-1].hora_inicio)}. Puedo enumerarlos. ¿Qué hora prefieres?")
            else: hablados.append(texto)
        elif t.tipo=="propuesta_cita":
            texto=f"Preparé una propuesta para el {fecha_hablada(t.fecha)}, de {hora_hablada(t.hora_inicio)} a {hora_hablada(t.hora_fin)}"+(f" con {_nombre(t.profesional.nombre)}" if t.profesional else "")+". Aún no está reservada: abre el formulario de citas y confirma. La disponibilidad se verificará de nuevo."
            escritos.append(texto); hablados.append(texto)
        elif t.tipo=="citas_propias":
            if not t.citas: texto="No encontré citas próximas programadas con los filtros aplicados. Si deseas, puedo ayudarte a consultar disponibilidad."
            else: texto="Tus próximas citas consultadas: "+"; ".join(f"el {fecha_hablada(c.fecha)} a las {hora_hablada(c.hora_inicio)} ({c.estado.lower()})" for c in t.citas)+"."
            escritos.append(texto)
            hablados.append(texto)
            if t.hay_mas:
                escritos.append("Hay más citas que no se incluyeron en esta página.")
                hablados.append("Hay más citas; puedes consultarlas en Mis citas.")
        elif t.tipo=="navegacion":
            nombres={"catalogo":"el catálogo", "agendar_cita":"el formulario de citas", "mis_citas":"Mis citas", "detalle_cita":"el detalle de tu cita", "reprogramar_cita":"el formulario de reprogramación", "cancelar_cita":"la opción de cancelación"}
            texto=f"Puedes abrir {nombres[t.destino]}." + (" Revisa los datos y confirma allí; no he realizado cambios." if t.destino in {"agendar_cita","reprogramar_cita","cancelar_cita"} else "")
            escritos.append(texto); hablados.append(texto)
    if incidencias:
        notas={"no_disponible":"No encontré la información solicitada en tu clínica.",
               "profesional_no_disponible":"No encontré ese profesional activo en tu clínica.",
               "servicio_no_disponible":"No encontré ese servicio activo en tu clínica.",
               "cita_no_disponible":"No encontré esa cita entre tus citas autorizadas.",
               "clinica_no_disponible":"No fue posible obtener la información institucional de tu clínica.",
               "ocupado":"Ese horario ya no está disponible. Elige otro horario.", "argumentos":"Necesito que aclares el profesional, la fecha o el horario para continuar."}
        for razon in dict.fromkeys(incidencias):
            texto=notas.get(razon,"No fue posible recuperar esa información. Inténtalo nuevamente.")
            escritos.append(texto); hablados.append(texto)
    return " ".join(escritos), para_voz(" ".join(hablados))
