"""Exception types. Failures are loud: tools re-raise these so MCP clients see them."""

from __future__ import annotations


class SocrataMCPError(Exception):
    """Base for all socrata-mcp errors."""


class ValidationError(SocrataMCPError):
    """A request was malformed before any network call was made."""


class PortalError(SocrataMCPError):
    """The portal rejected a request; carries the portal's actual message."""

    def __init__(self, message: str, *, status: int | None = None, url: str | None = None):
        self.portal_message = message
        self.status = status
        self.url = url
        detail = f"Portal error{f' (HTTP {status})' if status else ''}: {message}"
        if url:
            detail += f" [{url}]"
        super().__init__(detail)
