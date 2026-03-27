"""ResourceProvider protocol and provider registry."""

from typing import Optional, Protocol

from fastapi import FastAPI


class ResourceProvider(Protocol):
    """Protocol that each resource backend must implement."""

    @property
    def resource_type(self) -> str:
        """Unique identifier, e.g. 'gmail', 'ssh'."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for Signal notifications and approval page."""
        ...

    def validate_request(self, level: int, params: dict) -> Optional[str]:
        """Validate provider-specific grant request fields.

        Returns an error message on failure, None on success.
        """
        ...

    def default_duration(self, level: int) -> int:
        """Default duration in minutes for a given access level."""
        ...

    def format_signal_notification(self, grant: dict, approval_url: str) -> str:
        """Format the Signal message for an approval request."""
        ...

    def format_approval_details(self, grant: dict) -> str:
        """Return an HTML snippet for the approval page details card."""
        ...

    async def on_approved(self, grant: dict) -> None:
        """Called after a grant is activated. Provider-specific post-approval logic."""
        ...

    async def on_revoked(self, grant: dict) -> None:
        """Called when a grant is revoked or expires. Cleanup hook."""
        ...

    def register_routes(self, app: FastAPI) -> None:
        """Register provider-specific API routes on the FastAPI app."""
        ...

    async def startup(self) -> None:
        """Called during app lifespan startup. Load secrets, init clients."""
        ...


# ── Provider registry ─────────────────────────────────────────────────────

_providers: dict[str, ResourceProvider] = {}


def register_provider(provider: ResourceProvider) -> None:
    _providers[provider.resource_type] = provider


def get_provider(resource_type: str) -> Optional[ResourceProvider]:
    return _providers.get(resource_type)


def all_providers() -> dict[str, ResourceProvider]:
    return dict(_providers)
