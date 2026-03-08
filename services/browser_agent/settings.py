"""Environment-driven configuration for the Browser Agent."""

from pydantic_settings import SettingsConfigDict

from shared.llm import CommonAgentSettings


class BrowserAgentSettings(CommonAgentSettings):
    """Environment-driven configuration for the Browser Agent."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    browser_model: str = "qwen2.5vl:7b"  # multimodal model for direct screenshot inspection
    planner_model: str = "qwen3:8b"  # lighter model for planning/validation
    gateway_url: str = "http://gateway:8000"
    browser_tool_timeout: float = 65.0  # slightly longer than gateway timeout
