"""Resolve managed personal apps through the existing MCP and approval boundary.

A provider session is private to one connection and one tool ceiling. It never
selects the provider's most recent account or opens the unrestricted tool router.
"""

import logging
import re
from collections.abc import Sequence

from app.agents.mcp import McpServerSpec
from app.agents.spec import AgentSpec, McpApproval, PersonalMcpServerRef
from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.db.models.mcp_connection import McpConnection
from app.services.app_connections import (
    APPS,
    ComposioApp,
    ComposioClient,
    composio_user_id,
    get_app_by_catalog_key,
    is_configured,
    toolkit_from_catalog_key,
)

logger = logging.getLogger(__name__)

_ORIGIN = "https://backend.composio.dev"
_GUIDANCE = (
    "Connected apps belong to the person sending this message. Use only the account "
    "they name for the task; if several accounts fit, ask which one before reading. "
    "Identify the app and account when presenting results. An unavailable app is not "
    "an empty search result: explain what could not be checked. Offer connect_account "
    "only when their request needs that app. Some connected apps can change account "
    "data; follow the approval step for every such action and report completion only "
    "after the tool confirms it."
)


def with_app_connection_bindings(
    spec: AgentSpec, *, dynamic_catalog_keys: Sequence[str] = ()
) -> AgentSpec:
    """Add configured personal apps to a registered assistant's runtime copy.

    The caller verifies that this is the organization's registered assistant.
    Stored agent versions and explicitly configured personal bindings keep their
    existing ceilings. Dynamic catalog keys come only from this sender's managed
    app rows; account ownership is resolved again for every message.
    """
    configured = [app for app in APPS if is_configured(app.toolkit)]
    dynamic_keys = sorted(
        {
            key
            for key in dynamic_catalog_keys
            if get_app_by_catalog_key(key) is None and toolkit_from_catalog_key(key) is not None
        }
    )
    if not configured and not dynamic_keys:
        return spec
    dynamic_key_set = set(dynamic_keys)
    runtime_servers = [
        ref.model_copy(update={"approval": "all"})
        if (
            isinstance(ref, PersonalMcpServerRef)
            and ref.catalog_key in dynamic_key_set
            and ref.approval != "all"
        )
        else ref
        for ref in spec.mcp_servers
    ]
    bindings_changed = runtime_servers != spec.mcp_servers
    existing = {ref.catalog_key for ref in runtime_servers if isinstance(ref, PersonalMcpServerRef)}
    additions = [
        PersonalMcpServerRef(
            account="personal",
            catalog_key=app.catalog_key,
            allowed_tools=[action.name for action in app.actions if action.read_only],
            approval="writes",
        )
        for app in configured
        if app.catalog_key not in existing
    ]
    additions.extend(
        PersonalMcpServerRef(
            account="personal",
            catalog_key=key,
            allowed_tools=None,
            approval="all",
        )
        for key in dynamic_keys
        if key not in existing
    )
    instructions = spec.instructions
    if _GUIDANCE not in instructions:
        instructions = f"{instructions}\n\n{_GUIDANCE}".strip()
    if not additions and instructions == spec.instructions and not bindings_changed:
        return spec
    return spec.model_copy(
        update={"mcp_servers": [*runtime_servers, *additions], "instructions": instructions}
    )


async def managed_connection_spec(
    connection: McpConnection,
    *,
    name: str,
    allowed_tools: list[str] | None,
    approval: McpApproval,
) -> McpServerSpec | None:
    """One verified personal account, restricted to the reviewed read actions.

    The connection's local active status is necessary but not sufficient: the
    provider must still attest to the exact owner, toolkit, auth config, account,
    and active grant. A provider outage leaves this account unavailable this turn.
    """
    app = get_app_by_catalog_key(connection.catalog_key or "")
    dynamic = app is None
    toolkit = toolkit_from_catalog_key(connection.catalog_key)
    if dynamic and toolkit is not None and toolkit != "composio":
        app = ComposioApp(
            toolkit=toolkit,
            name=toolkit.replace("-", " ").replace("_", " ").title(),
            description="Use this connected app account.",
            category="apps",
            actions=(),
        )
    if (
        app is None
        or connection.auth_type != "composio"
        or connection.scope != "user"
        or connection.organization_id is not None
        or connection.purpose != "mcp"
        or connection.user_id is None
        or not connection.is_enabled
        or connection.composio_status != "ACTIVE"
        or not connection.composio_account_id
        or not connection.composio_auth_config_id
        or not (
            is_configured(app.toolkit)
            if not dynamic
            else settings.COMPOSIO_API_KEY is not None
            and bool(settings.COMPOSIO_API_KEY.get_secret_value().strip())
        )
    ):
        return None
    if dynamic and connection.url != f"{_ORIGIN}/api/v3.1/toolkits/{app.toolkit}":
        return None
    # A catalog change must not silently expand a person's previously reviewed
    # tool ceiling. The runtime independently intersects all three boundaries.
    if dynamic:
        tools = connection.allowed_tools or []
        if (
            not tools
            or len(tools) > 1000
            or any(
                not isinstance(tool, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", tool) is None
                or tool.upper().startswith("COMPOSIO_")
                for tool in tools
            )
            or len(set(tools)) != len(tools)
        ):
            return None
    else:
        tools = [action.name for action in app.actions if action.read_only]
    for ceiling in (connection.allowed_tools, allowed_tools):
        if ceiling is not None:
            within = set(ceiling)
            tools = [tool for tool in tools if tool in within]
    if not tools:
        return None
    user_id = composio_user_id(connection.user_id)
    try:
        async with ComposioClient() as client:
            verified = await client.verify_account(
                user_id=user_id,
                toolkit=app.toolkit,
                account_id=connection.composio_account_id,
                auth_config_id=connection.composio_auth_config_id,
            )
            if (
                verified.status != "ACTIVE"
                or verified.is_disabled
                or verified.auth_config.is_disabled
            ):
                return None
            url = await client.create_session(
                user_id=user_id,
                toolkit=app.toolkit,
                account_id=connection.composio_account_id,
                auth_config_id=connection.composio_auth_config_id,
                tools=tools,
            )
            headers = client.mcp_headers()
    except ExternalServiceError:
        logger.info("Managed app %s is unavailable for this turn", app.toolkit)
        return None
    account_name = connection.account_email or connection.label or app.name
    if (
        connection.account_email
        and connection.label
        and connection.account_email != connection.label
    ):
        account_name = f"{connection.label} · {connection.account_email}"
    return McpServerSpec(
        name=name,
        url=url,
        headers=headers,
        allowed_tools=tools,
        approval="all" if dynamic or approval == "all" else "writes",
        include_instructions=False,
        account=account_name,
        source_connection_id=connection.id,
        authorization_generation=connection.authorization_generation,
        source_url=connection.url,
        credential_origin=_ORIGIN,
    )
