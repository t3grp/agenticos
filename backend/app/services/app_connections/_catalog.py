"""Reviewed app actions offered by the managed connection journey.

The allowlist is a product policy, not the provider's best-effort tool hints.
Adding a write requires the corresponding T3OS approval and execution tests.
Tool names were checked against Composio's official toolkit catalog on 2026-11-09.
"""

import re
from dataclasses import dataclass

from app.core.exceptions import BadRequestError


@dataclass(frozen=True)
class ComposioAction:
    name: str
    label: str
    read_only: bool = True


@dataclass(frozen=True)
class ComposioApp:
    toolkit: str
    name: str
    description: str
    category: str
    actions: tuple[ComposioAction, ...]

    @property
    def catalog_key(self) -> str:
        return f"composio-{self.toolkit}"


APPS: tuple[ComposioApp, ...] = (
    ComposioApp(
        "gmail",
        "Gmail",
        "Find email and prepare replies in your conversation.",
        "communication",
        (
            ComposioAction("GMAIL_FETCH_EMAILS", "Find email"),
            ComposioAction("GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID", "Read an email"),
            ComposioAction("GMAIL_FETCH_MESSAGE_BY_THREAD_ID", "Read an email thread"),
        ),
    ),
    ComposioApp(
        "googledrive",
        "Google Drive",
        "Find files and read documents you can access.",
        "knowledge",
        (
            ComposioAction("GOOGLEDRIVE_FIND_FILE", "Find files"),
            ComposioAction("GOOGLEDRIVE_GET_FILE_METADATA", "Read file details"),
            ComposioAction("GOOGLEDRIVE_GET_DOCUMENT", "Read a document"),
        ),
    ),
    ComposioApp(
        "googlecalendar",
        "Google Calendar",
        "Review upcoming meetings and available time.",
        "planning",
        (
            ComposioAction("GOOGLECALENDAR_EVENTS_LIST", "List meetings"),
            ComposioAction("GOOGLECALENDAR_EVENTS_GET", "Read a meeting"),
            ComposioAction("GOOGLECALENDAR_FIND_FREE_SLOTS", "Find available time"),
        ),
    ),
    ComposioApp(
        "outlook",
        "Outlook",
        "Review messages and meetings from Microsoft 365.",
        "communication",
        (
            ComposioAction("OUTLOOK_LIST_MESSAGES", "List email"),
            ComposioAction("OUTLOOK_SEARCH_MESSAGES", "Find email"),
            ComposioAction("OUTLOOK_LIST_EVENTS", "List meetings"),
        ),
    ),
    ComposioApp(
        "slack",
        "Slack",
        "Find discussions and catch up on conversation threads.",
        "communication",
        (
            ComposioAction("SLACK_SEARCH_MESSAGES", "Find messages"),
            ComposioAction("SLACK_FETCH_CONVERSATION_HISTORY", "Read a channel"),
            ComposioAction("SLACK_FETCH_MESSAGE_THREAD_FROM_A_CONVERSATION", "Read a thread"),
        ),
    ),
    ComposioApp(
        "notion",
        "Notion",
        "Find pages and read the knowledge shared with your account.",
        "knowledge",
        (
            ComposioAction("NOTION_SEARCH_NOTION_PAGE", "Find pages"),
            ComposioAction("NOTION_RETRIEVE_PAGE", "Read page details"),
            ComposioAction("NOTION_GET_PAGE_MARKDOWN", "Read page content"),
        ),
    ),
    ComposioApp(
        "github",
        "GitHub",
        "Find repositories, issues and pull requests.",
        "development",
        (
            ComposioAction("GITHUB_SEARCH_REPOSITORIES", "Find repositories"),
            ComposioAction(
                "GITHUB_SEARCH_ISSUES_AND_PULL_REQUESTS", "Find issues and pull requests"
            ),
            ComposioAction("GITHUB_SEARCH_CODE", "Find code"),
        ),
    ),
)

_TOOLKIT_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,54}$")


def get_app(toolkit: str) -> ComposioApp:
    for app in APPS:
        if app.toolkit == toolkit:
            return app
    raise BadRequestError(
        message="This app is not available to connect.", details={"toolkit": toolkit}
    )


def get_app_by_catalog_key(key: str | None) -> ComposioApp | None:
    return next((app for app in APPS if app.catalog_key == key), None)


def toolkit_from_catalog_key(key: str | None) -> str | None:
    """Read a syntactically valid Composio key without blessing it for publish.

    Public agent bindings remain validated against the reviewed static catalog.
    This parser is for rows the managed app service created after checking the
    official toolkit directory and is not an authorization decision on its own.
    """
    if key is None or not key.startswith("composio-"):
        return None
    toolkit = key.removeprefix("composio-")
    return toolkit if _TOOLKIT_SLUG.fullmatch(toolkit) else None
