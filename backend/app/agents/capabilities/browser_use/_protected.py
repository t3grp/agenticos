"""A sticky output boundary: an unprovably safe page never resumes capture."""

from app.agents.browser_events import BrowserEvent
from app.agents.capabilities.browser_use._elements import Snapshot
from app.core.browser_origins import browser_origin

PROTECTED_TITLE = "Protected browser session"
PROTECTED_TEXT = (
    "Page output is hidden after a credential fill. Close this tab to end protection; "
    "a navigation or form submission does not make the page safe to capture."
)


class BrowserOutputGuard:
    """Installed before any value is opened, and never reset on this page object."""

    def __init__(self) -> None:
        self._origin: str | None = None

    @property
    def active(self) -> bool:
        return self._origin is not None

    def protect(self, origin: str) -> None:
        normalized = browser_origin(origin)
        if self._origin is None:
            self._origin = normalized

    def location(self) -> tuple[str, str]:
        return self._origin or "about:blank", PROTECTED_TITLE

    def snapshot(self) -> Snapshot:
        url, title = self.location()
        return Snapshot(url=url, title=title, elements=(), text=PROTECTED_TEXT)

    def event(self, event: BrowserEvent) -> BrowserEvent:
        """Rebuild from an allowlist, so future event fields cannot bypass protection."""
        url, title = self.location()
        return BrowserEvent(
            kind=event.kind,
            call_id=event.call_id,
            step=event.step,
            tab_id=event.tab_id,
            run_id=event.run_id,
            outcome=event.outcome,
            credential_plan_id=event.credential_plan_id,
            credential_operation=event.credential_operation,
            url=url,
            title=title,
            reason=PROTECTED_TEXT,
        )
