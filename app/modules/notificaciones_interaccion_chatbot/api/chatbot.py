from dataclasses import dataclass
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.core import config
from app.core.dependencies import get_db, get_tenant_claims, obtener_usuario_actual, nombre_rol_actual
from app.modules.gestion_pacientes.repositories.repository import obtener_paciente_por_usuario_id
from app.modules.notificaciones_interaccion_chatbot.schemas.chatbot import (
    Entrada, MensajeEntrada, ConversacionRespuesta, MensajeRespuesta, ErrorChatbot,
)
from app.modules.notificaciones_interaccion_chatbot.services.memoria import MemoriaConversaciones
from app.modules.notificaciones_interaccion_chatbot.services.proveedor import ProveedorConversacional
from app.modules.notificaciones_interaccion_chatbot.casos_uso.cu41_interactuar_chatbot import conversar

class RutaAcotada(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def acotada(request):
            partes, total = [], 0
            async for parte in request.stream():
                total += len(parte)
                if total > 20000:
                    raise HTTPException(413, "Solicitud del chatbot demasiado extensa")
                partes.append(parte)
            request._body = b"".join(partes)
            return await original(request)
        return acotada


RESPUESTAS = {code:{"model":ErrorChatbot,"description":desc} for code,desc in {
    401:"JWT inválido, expirado o usuario inactivo",403:"Rol o perfil no autorizado",
    404:"Conversación o perfil no disponible",409:"Conversación ocupada",413:"Cuerpo excesivo",
    429:"Límite temporal de recursos o consumo",502:"Respuesta del proveedor inválida",
    503:"Chatbot deshabilitado o proveedor indisponible",504:"Presupuesto de tiempo agotado"}.items()}
for code,detalle in {
    401:"Token inválido o expirado",403:"Perfil de paciente inactivo",404:"Conversación no disponible",
    409:"Conversación ocupada; vuelve a intentarlo",413:"Solicitud del chatbot demasiado extensa",
    429:"Límite de consumo del chatbot",502:"Respuesta del chatbot no disponible",
    503:"El chatbot no está habilitado",504:"Se agotó el tiempo de la consulta al chatbot",
}.items():
    RESPUESTAS[code]["content"]={"application/json":{"example":{"detail":detalle}}}
RESPUESTAS[422]={"description":"Validación de entrada o herramienta: detail puede ser texto o lista de errores",
                 "content":{"application/json":{"schema":{"type":"object","properties":{"detail":{"anyOf":[{"type":"string"},{"type":"array","items":{"type":"object"}}]}}},"example":{"detail":"Argumentos de herramienta inválidos"}}}}
router = APIRouter(prefix="/conversaciones", tags=["CU41 - Chatbot de pacientes"], route_class=RutaAcotada, responses=RESPUESTAS)
_memoria = MemoriaConversaciones()


def configuracion():
    return config.CHATBOT_CONFIG


def memoria():
    return _memoria


def proveedor():
    return ProveedorConversacional()


def habilitado(cfg=Depends(configuracion)):
    if not cfg.enabled or not cfg.deepseek_api_key.get_secret_value().strip():
        raise HTTPException(503, "El chatbot no está habilitado")
    return cfg


@dataclass(frozen=True)
class Actor:
    propietario: tuple
    empresa: int
    codigo: str
    usuario: object


def paciente_actual(request: Request, claims=Depends(get_tenant_claims),
                    usuario=Depends(obtener_usuario_actual), db: Session = Depends(get_db)):
    if nombre_rol_actual(usuario) != "paciente" or not usuario.rol.estado:
        raise HTTPException(403, "Solo pacientes con rol activo pueden utilizar el chatbot")
    paciente = obtener_paciente_por_usuario_id(db, usuario.id)
    if paciente is None:
        raise HTTPException(404, "Perfil de paciente no encontrado")
    if not paciente.estado:
        raise HTTPException(403, "Perfil de paciente inactivo")
    contexto = db.info.get("tenant_context")
    if contexto is None or contexto.empresa_id != claims["empresa_id"]:
        raise HTTPException(401, "Contexto tenant no válido")
    # Only a digest of the signed session token; raw JWT never enters memory/provider.
    sesion = sha256(request.headers["authorization"].split()[-1].encode()).hexdigest()
    owner = (contexto.tenant_database_id, contexto.empresa_id, usuario.id, paciente.id, sesion)
    actor = Actor(owner, contexto.empresa_id, contexto.empresa_codigo,
                  SimpleNamespace(id=usuario.id, rol=SimpleNamespace(nombre=usuario.rol.nombre)))
    db.rollback()  # End authentication read transaction BEFORE any external await.
    return actor


@router.post("", response_model=ConversacionRespuesta, status_code=201)
def crear(datos: Entrada, actor=Depends(paciente_actual), cfg=Depends(habilitado), store=Depends(memoria)):
    store.consumir(actor.propietario, cfg)
    c = store.crear(actor.propietario, cfg)
    return ConversacionRespuesta(conversacion_id=c.id, expira_en_segundos=cfg.session_ttl_seconds)


@router.post("/{conversacion_id}/mensajes", response_model=MensajeRespuesta)
async def mensaje(conversacion_id: UUID, datos: MensajeEntrada,
                  actor=Depends(paciente_actual), cfg=Depends(habilitado),
                  store=Depends(memoria), model=Depends(proveedor), db: Session = Depends(get_db)):
    if len(datos.mensaje) > cfg.max_message_length:
        raise HTTPException(422, "El mensaje supera el límite permitido")
    store.consumir(actor.propietario, cfg)
    with store.usar(conversacion_id, actor.propietario, cfg) as c:
        return await conversar(c, datos, actor, db, cfg, store, model)


@router.delete("/{conversacion_id}", status_code=204)
def finalizar(conversacion_id: UUID, actor=Depends(paciente_actual),
              cfg=Depends(habilitado), store=Depends(memoria)):
    with store.usar(conversacion_id, actor.propietario, cfg) as c:
        store.borrar(c)
    return Response(status_code=204)
