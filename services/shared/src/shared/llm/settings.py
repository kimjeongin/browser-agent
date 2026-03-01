"""Pydantic Settings for LLM configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """Centralised LLM configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_base_url: str = "http://host.docker.internal:11434"
    orchestrator_model: str = "qwen3:8b"
    browser_agent_model: str = "qwen3:14b"
    chat_agent_model: str = "qwen3:8b"
    vision_model: str = "qwen3vl:8b"
    llm_temperature: float = 0.0
    llm_num_ctx: int = 8192
