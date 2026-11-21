"""Chat Completions adapter independent of integracion_ia.generar_json."""
import asyncio
from typing import Literal

from fastapi import HTTPException
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Funcion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    arguments: str = Field(max_length=4096)


class Llamada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    type: Literal["function"] = "function"
    function: Funcion


class TurnoModelo(BaseModel):
    content: str | None = Field(default=None, max_length=3000)
    tool_calls: list[Llamada] = Field(default_factory=list, max_length=16)


def validar_turno(value):
    try:
        turno = TurnoModelo.model_validate(value)
        if turno.content is not None:
            turno.content.encode("utf-8")
        if not turno.tool_calls and not (turno.content and turno.content.strip()):
            raise ValueError("empty")
        if len({x.id for x in turno.tool_calls}) != len(turno.tool_calls):
            raise ValueError("duplicate")
        return turno
    except (ValueError, ValidationError):
        raise HTTPException(502, "Respuesta del chatbot no disponible") from None


class ProveedorConversacional:
    async def completar(self, mensajes, herramientas, cfg, timeout):
        try:
            async with AsyncOpenAI(
                api_key=cfg.deepseek_api_key.get_secret_value(),
                base_url=cfg.deepseek_base_url, max_retries=0,
                timeout=min(timeout, cfg.deepseek_timeout_seconds),
            ) as client:
                resultado = await client.chat.completions.create(
                    model=cfg.deepseek_model, messages=mensajes, tools=herramientas,
                    max_tokens=cfg.max_output_tokens, stream=False,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                if not resultado.choices or resultado.choices[0].finish_reason not in {"stop", "tool_calls"}:
                    raise HTTPException(502, "Respuesta del chatbot no disponible")
                m = resultado.choices[0].message
                # Explicit projection: never return reasoning_content or debug fields.
                return validar_turno({"content": m.content, "tool_calls": [
                    {"id": t.id, "type": t.type,
                     "function": {"name": t.function.name, "arguments": t.function.arguments}}
                    for t in (m.tool_calls or [])]})
        except (OpenAIError, asyncio.TimeoutError):
            raise HTTPException(503, "El proveedor del chatbot no está disponible") from None
        except (AttributeError, IndexError, TypeError):
            raise HTTPException(502, "Respuesta del chatbot no disponible") from None
