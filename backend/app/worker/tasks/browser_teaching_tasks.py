"""Scheduled retention for transient browser teaching steps."""

from app.services.browser_teaching import sweep_expired


async def sweep_browser_teachings() -> int:
    """Erase session steps after their 24-hour review window."""
    return await sweep_expired()
