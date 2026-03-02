"""Environment-driven configuration for the Browser Agent."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class BrowserAgentSettings(BaseSettings):
    """Environment-driven configuration for the Browser Agent."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://postgres:password@postgres:5432/browser_agent"
    )
    browser_model: str = "qwen3:14b"
    planner_model: str = "qwen3:8b"  # lighter model for planning/validation
    vision_model: str = "qwen3vl:8b"  # vision-language model for DOM fallback
    gateway_url: str = "http://gateway:8000"
    browser_tool_timeout: float = 65.0  # slightly longer than gateway timeout
