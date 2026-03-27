"""Pydantic models for API requests."""

from typing import Optional

from pydantic import BaseModel


class GrantRequest(BaseModel):
    model_config = {"extra": "ignore"}

    resourceType: str = "gmail"
    level: int
    description: str
    durationMinutes: Optional[int] = None
    callback: bool = True
    callbackSessionKey: Optional[str] = None

    # Gmail-specific (kept at top level for backward compat)
    messageId: Optional[str] = None
    query: Optional[str] = None

    # SSH-specific
    host: Optional[str] = None
    principal: Optional[str] = None
    hostGroup: Optional[str] = None
    role: Optional[str] = None
    publicKey: Optional[str] = None


class SSHCredentialRequest(BaseModel):
    model_config = {"extra": "ignore"}

    grantId: str
    publicKey: str
