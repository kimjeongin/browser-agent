"""Session domain models."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Session(BaseModel):
    """Represents an active user session."""

    session_id: str
    user_id: str  # Keycloak ``sub`` claim
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SessionCreate(BaseModel):
    """Payload for creating a new session."""

    user_id: str
