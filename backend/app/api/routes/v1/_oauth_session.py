"""Session markers for the OAuth browser handoff."""

from fastapi import Request

CLIENT_SESSION_KEY = "oauth_clients"
DESKTOP_CLIENT = "desktop"
MAX_PENDING = 4


def started_client(request: Request) -> dict[str, str] | None:
    """Read the client marker for the state currently in the callback."""
    pending = request.session.get(CLIENT_SESSION_KEY) or {}
    started = pending.get(request.query_params.get("state") or "", {})
    return started if isinstance(started, dict) else None


def take_started_client(request: Request) -> dict[str, str] | None:
    """Consume the marker for this OAuth state, if it had one."""
    pending = dict(request.session.get(CLIENT_SESSION_KEY) or {})
    started = pending.pop(request.query_params.get("state") or "", None)
    if pending:
        request.session[CLIENT_SESSION_KEY] = pending
    else:
        request.session.pop(CLIENT_SESSION_KEY, None)
    return started if isinstance(started, dict) else None
