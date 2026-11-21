"""Independent, optional CU41 configuration. No fallback to clinical IA secrets."""
import os

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ChatbotSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = False
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = Field(default="deepseek-flash", min_length=1, max_length=80)
    deepseek_timeout_seconds: float = Field(default=30, ge=1, le=60)
    max_output_tokens: int = Field(default=512, ge=32, le=2048)
    max_history_messages: int = Field(default=12, ge=2, le=40)
    max_tool_iterations: int = Field(default=5, ge=1, le=8)
    max_message_length: int = Field(default=1000, ge=10, le=4000)
    rate_limit_per_minute: int = Field(default=10, ge=1, le=60)
    tenant_rate_limit_per_minute: int = Field(default=100, ge=1, le=1000)
    global_rate_limit_per_minute: int = Field(default=500, ge=1, le=5000)
    max_tool_calls: int = Field(default=10, ge=1, le=16)
    request_timeout_seconds: float = Field(default=60, ge=1, le=120)
    max_context_bytes: int = Field(default=32768, ge=8192, le=131072)
    max_tool_arguments_bytes: int = Field(default=2048, ge=128, le=4096)
    max_tool_result_bytes: int = Field(default=8192, ge=256, le=16384)
    session_ttl_seconds: int = Field(default=1800, ge=1, le=7200)
    max_sessions: int = Field(default=1000, ge=1, le=2000)
    max_sessions_per_patient: int = Field(default=5, ge=1, le=10)
    max_memory_bytes: int = Field(default=16777216, ge=8192, le=67108864)
    max_concurrent_requests: int = Field(default=8, ge=1, le=32)

    @field_validator("deepseek_base_url")
    @classmethod
    def trusted_provider(cls, value):
        if value.rstrip("/") != "https://api.deepseek.com":
            raise ValueError("CU41 solo permite el proveedor HTTPS autorizado")
        return value.rstrip("/")

    @classmethod
    def from_env(cls):
        # Validation errors must never interpolate the real secret/environment.
        values = {name: os.environ[f"CHATBOT_{name.upper()}"]
                  for name in cls.model_fields if f"CHATBOT_{name.upper()}" in os.environ}
        try:
            return cls(**values)
        except ValueError:
            raise RuntimeError("Configuración CHATBOT inválida; revise tipos y límites") from None
