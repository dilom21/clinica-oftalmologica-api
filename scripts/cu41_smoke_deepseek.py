"""Optional BILLABLE synthetic smoke. Never run automatically or enable chatbot.

python -m scripts.cu41_smoke_deepseek --confirm-cost [--env-file PATH]
Only CHATBOT_* variables are selected; no existing clinical IA secret fallback.
"""
import argparse
import asyncio
import json
import os

from dotenv import dotenv_values

from app.modules.notificaciones_interaccion_chatbot.settings import ChatbotSettings
from app.modules.notificaciones_interaccion_chatbot.services.proveedor import ProveedorConversacional


async def comprobar(cfg):
    if not cfg.deepseek_api_key.get_secret_value():
        raise SystemExit("Falta CHATBOT_DEEPSEEK_API_KEY")
    model = ProveedorConversacional()
    tools = [{"type":"function","function":{"name":"clinica_ficticia","description":"Datos de una clínica totalmente ficticia",
              "parameters":{"type":"object","properties":{},"additionalProperties":False}}}]
    mensajes = [{"role":"system","content":"Demostración ficticia. Consulta clinica_ficticia y saluda brevemente. Sin datos reales."},
                {"role":"user","content":"Información de la clínica ficticia, por favor."}]
    cfg = cfg.model_copy(update={"max_output_tokens":64})
    async with asyncio.timeout(35):
        turno = await model.completar(mensajes,tools,cfg,15)
        if turno.tool_calls:
            if any(t.function.name != "clinica_ficticia" or json.loads(t.function.arguments) != {} for t in turno.tool_calls):
                raise SystemExit("Herramienta inesperada; prueba detenida")
            mensajes.append({"role":"assistant","content":None,"tool_calls":[t.model_dump() for t in turno.tool_calls]})
            for t in turno.tool_calls:
                mensajes.append({"role":"tool","tool_call_id":t.id,"content":'{"nombre":"Clínica ficticia de demostración"}'})
            await model.completar(mensajes,tools,cfg,15)
    print("Conexión sintética completada. No se accedió a bases de datos; chatbot no activado.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-cost", action="store_true")
    parser.add_argument("--env-file")
    args = parser.parse_args()
    if not args.confirm_cost:
        parser.error("Se requiere autorización explícita --confirm-cost para consumir API real")
    permitidas = {"deepseek_api_key", "deepseek_base_url", "deepseek_model", "deepseek_timeout_seconds"}
    source = dotenv_values(args.env_file, interpolate=False) if args.env_file else os.environ
    values = {n:source["CHATBOT_"+n.upper()] for n in permitidas if source.get("CHATBOT_"+n.upper())}
    try:
        asyncio.run(comprobar(ChatbotSettings(**values)))
    except Exception:
        raise SystemExit("La comprobación no pudo completarse; detalle interno omitido por seguridad") from None


if __name__ == "__main__":
    main()
