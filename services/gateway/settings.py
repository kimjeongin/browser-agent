"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://postgres:password@postgres:5432/browser_agent"
    )
    orchestrator_url: str = "http://orchestrator:8001"
    # Public realm URL -- must match the ``iss`` claim in JWTs issued by Keycloak.
    keycloak_realm_url: str = "http://localhost:8080/realms/browser-agent"
    # Internal URL for fetching JWKS keys.
    keycloak_jwks_url: str = ""
    keycloak_audience: str = "browser-agent-extension"
    session_ttl: int = 86400  # 24 hours
    browser_tool_timeout: float = 60.0  # seconds to wait for extension result
