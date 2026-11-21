"""The audit trail in plain language: sentences and name resolution.

The log stores UUIDs and machine action codes. The feed reads sentences, so
the rendering is a property of the read path - and of these tests, not of the
console that happens to display it.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.permissions import AuthContext, OrgRoleName
from app.services.audit import AuditService, render_audit_sentence

pytestmark = [pytest.mark.anyio, pytest.mark.security]

_ACTOR = uuid.uuid4()
_AGENT = uuid.uuid4()
_SUBJECT = uuid.uuid4()
_RUN = uuid.uuid4()


def _entry(
    *,
    action: str,
    actor: uuid.UUID | None = _ACTOR,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        actor_user_id=actor,
        impersonator_user_id=None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
        created_at=datetime.now(UTC),
    )


_NAMES = {
    str(_ACTOR): "Sherman Tan",
    str(_SUBJECT): "Ada Lovelace",
    str(_AGENT): "Architect",
}


def test_a_tool_call_reads_as_who_used_what_and_whether_it_was_approved() -> None:
    entry = _entry(
        action="agent.tool_executed",
        target_type="agent_run",
        target_id=str(_RUN),
        details={
            "run_id": str(_RUN),
            "agent_id": str(_AGENT),
            "agent_name": "Architect",
            "tool_name": "send_email",
            "tool_call_id": "c1",
            "approved": True,
            "cost_usd": "0.42",
        },
    )

    assert (
        render_audit_sentence(entry, _NAMES)
        == "Architect used send email after approval (run cost $0.42)."
    )


def test_an_unapproved_tool_call_names_no_approver_and_no_cost() -> None:
    entry = _entry(
        action="agent.tool_executed",
        target_type="agent_run",
        target_id=str(_RUN),
        details={
            "run_id": str(_RUN),
            "agent_id": str(_AGENT),
            "agent_name": "Architect",
            "tool_name": "web_search",
            "tool_call_id": "c2",
            "approved": False,
        },
    )

    assert render_audit_sentence(entry, _NAMES) == "Architect used web search."


def test_a_tool_call_with_no_agent_and_no_tool_stays_readable() -> None:
    entry = _entry(action="agent.tool_executed", target_type="agent_run", target_id=str(_RUN))

    assert render_audit_sentence(entry, {}) == "An agent used a tool."


def test_an_approval_names_the_decider_and_the_tool() -> None:
    entry = _entry(
        action="approval.approved",
        target_type="tool_approval",
        target_id=str(uuid.uuid4()),
        details={"tool_id": "send_email", "run_id": str(_RUN)},
    )

    assert render_audit_sentence(entry, _NAMES) == "Sherman Tan approved a send email call."


def test_a_rejected_approval_names_the_rejecter() -> None:
    entry = _entry(
        action="approval.rejected",
        details={"tool_id": "delete_row", "run_id": str(_RUN)},
    )

    assert render_audit_sentence(entry, _NAMES) == "Sherman Tan rejected a delete row call."


def test_an_expired_approval_says_nobody_decided() -> None:
    entry = _entry(
        action="approval.expired",
        actor=None,
        details={"tool_id": "send_email", "run_id": str(_RUN)},
    )

    assert render_audit_sentence(entry, _NAMES) == "A send email approval expired with no decision."


def test_a_workflow_decision_reads_as_a_proposal_not_a_tool_call() -> None:
    entry = _entry(action="workflow_approval.approved")

    assert render_audit_sentence(entry, _NAMES) == "Sherman Tan approved a workflow proposal."


def test_a_share_names_the_person_the_level_and_the_expiry() -> None:
    entry = _entry(
        action="resource.share",
        target_type="table",
        target_id=str(uuid.uuid4()),
        details={
            "subject_user_id": str(_SUBJECT),
            "level": "read",
            "expires_at": "2026-12-31T00:00:00+00:00",
        },
    )

    assert (
        render_audit_sentence(entry, _NAMES)
        == "Sherman Tan shared the table with Ada Lovelace with read access, until 2026-12-31."
    )


def test_a_group_share_says_a_group_rather_than_a_uuid() -> None:
    entry = _entry(
        action="resource.share",
        target_type="agent",
        target_id=str(_AGENT),
        details={"subject_group_id": str(uuid.uuid4()), "level": "use"},
    )

    assert (
        render_audit_sentence(entry, _NAMES)
        == "Sherman Tan shared Architect with a group with use access."
    )


def test_an_unshare_and_a_visibility_change_name_the_target() -> None:
    assert (
        render_audit_sentence(
            _entry(action="resource.unshare", target_type="table", target_id=str(uuid.uuid4())),
            _NAMES,
        )
        == "Sherman Tan removed access to the table."
    )
    assert (
        render_audit_sentence(
            _entry(
                action="resource.visibility_changed",
                target_type="table",
                target_id=str(uuid.uuid4()),
            ),
            _NAMES,
        )
        == "Sherman Tan changed who can see the table."
    )


def test_agent_lifecycle_names_the_agent() -> None:
    assert (
        render_audit_sentence(
            _entry(action="agent.created", target_type="agent", target_id=str(_AGENT)),
            _NAMES,
        )
        == "Sherman Tan created agent Architect."
    )
    assert (
        render_audit_sentence(
            _entry(
                action="agent.published",
                target_type="agent",
                target_id=str(_AGENT),
                details={"name": "Architect"},
            ),
            _NAMES,
        )
        == "Sherman Tan published agent Architect."
    )
    assert (
        render_audit_sentence(
            _entry(action="agent.rolled_back", target_type="agent", target_id=str(_AGENT)),
            _NAMES,
        )
        == "Sherman Tan rolled back agent Architect."
    )


def test_a_run_cancelled_by_a_workflow_says_so() -> None:
    assert (
        render_audit_sentence(
            _entry(
                action="run.cancelled",
                actor=None,
                target_type="agent_run",
                target_id=str(_RUN),
                details={"previous_status": "awaiting_approval", "by": "workflow_run"},
            ),
            _NAMES,
        )
        == "A run was cancelled because its workflow run ended."
    )
    assert (
        render_audit_sentence(
            _entry(action="run.cancelled", target_type="agent_run", target_id=str(_RUN)),
            _NAMES,
        )
        == "Sherman Tan cancelled a run."
    )
    assert (
        render_audit_sentence(
            _entry(action="run.retried", target_type="agent_run", target_id=str(_RUN)),
            _NAMES,
        )
        == "Sherman Tan retried a run."
    )


def test_an_export_names_the_format_and_the_row_count() -> None:
    assert (
        render_audit_sentence(
            _entry(
                action="audit.export",
                target_type="audit_log",
                details={"format": "csv", "row_count": 42},
            ),
            _NAMES,
        )
        == "Sherman Tan exported the audit trail (csv, 42 rows)."
    )
    assert (
        render_audit_sentence(
            _entry(action="runs.export", details={"format": "csv", "row_count": 7}),
            _NAMES,
        )
        == "Sherman Tan exported runs."
    )


def test_a_role_change_names_the_new_role_where_the_entry_carries_it() -> None:
    assert (
        render_audit_sentence(
            _entry(action="member.role_changed", details={"new_role": "admin"}),
            _NAMES,
        )
        == "Sherman Tan changed a member's role to admin."
    )


def test_an_unknown_action_is_named_not_paraphrased() -> None:
    assert (
        render_audit_sentence(_entry(action="frobnicate.wobble"), _NAMES)
        == "Sherman Tan performed frobnicate.wobble."
    )


def test_a_generic_verb_falls_back_to_the_target_type() -> None:
    assert (
        render_audit_sentence(
            _entry(action="table.created", target_type="table", target_id=str(uuid.uuid4())),
            _NAMES,
        )
        == "Sherman Tan created the table."
    )


def test_an_unresolved_actor_reads_as_someone_not_a_uuid() -> None:
    entry = _entry(action="agent.published", target_type="agent", target_id=str(_AGENT))

    assert render_audit_sentence(entry, {}) == "Someone published an agent."


def _ctx(org_id: uuid.UUID | None = None) -> AuthContext:
    return AuthContext(
        user_id=uuid.uuid4(),
        organization_id=org_id or uuid.uuid4(),
        role=OrgRoleName.OWNER,
    )


async def test_the_list_resolves_actor_and_agent_names_for_the_feed() -> None:
    """The read path, not the renderer alone: a page of entries arrives with
    the names on it, so the feed never shows a UUID."""
    actor = SimpleNamespace(id=_ACTOR, full_name="Sherman Tan", email="sherman@example.com")
    agent = SimpleNamespace(id=_AGENT, name="Architect")
    entry = MagicMock()
    entry.id = uuid.uuid4()
    entry.actor_user_id = _ACTOR
    entry.impersonator_user_id = None
    entry.action = "agent.published"
    entry.target_type = "agent"
    entry.target_id = str(_AGENT)
    entry.details = {"name": "Architect"}
    entry.created_at = datetime.now(UTC)

    with (
        patch(
            "app.services.audit.audit_log_repo.list_for_org",
            new=AsyncMock(return_value=[entry]),
        ),
        patch("app.services.audit.audit_log_repo.count_for_org", new=AsyncMock(return_value=1)),
        patch(
            "app.services.audit.user_repo.get_by_ids",
            new=AsyncMock(return_value=[actor]),
        ),
        patch(
            "app.services.audit.agent_repo.list_by_ids",
            new=AsyncMock(return_value=([agent], 1)),
        ),
    ):
        page = await AuditService(MagicMock()).list_for_organization(_ctx())

    assert page.items[0].actor_name == "Sherman Tan"
    assert page.items[0].sentence == "Sherman Tan published agent Architect."


async def test_the_list_falls_back_to_email_where_a_user_has_no_name() -> None:
    actor = SimpleNamespace(id=_ACTOR, full_name=None, email="sherman@example.com")
    entry = MagicMock()
    entry.id = uuid.uuid4()
    entry.actor_user_id = _ACTOR
    entry.impersonator_user_id = None
    entry.action = "agent.created"
    entry.target_type = "agent"
    entry.target_id = str(_AGENT)
    entry.details = {}
    entry.created_at = datetime.now(UTC)

    with (
        patch(
            "app.services.audit.audit_log_repo.list_for_org",
            new=AsyncMock(return_value=[entry]),
        ),
        patch("app.services.audit.audit_log_repo.count_for_org", new=AsyncMock(return_value=1)),
        patch(
            "app.services.audit.user_repo.get_by_ids",
            new=AsyncMock(return_value=[actor]),
        ),
        patch(
            "app.services.audit.agent_repo.list_by_ids",
            new=AsyncMock(return_value=([], 0)),
        ),
    ):
        page = await AuditService(MagicMock()).list_for_organization(_ctx())

    assert page.items[0].actor_name == "sherman@example.com"
