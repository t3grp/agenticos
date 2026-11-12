"""Tests for automatic skill learning — the step after an approval.

The things worth guarding: learning a proposal twice writes nothing the
second time, a skill somebody rewrote since the proposal was written is
refused rather than overwritten, a new skill is created through the one skill
writer with its provenance audited, and a name collision with different
content is refused rather than renamed silently. No database here: the skill
catalog and the registry are fakes, and the audit trail is a mock.
"""

import hashlib
import uuid
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.core.exceptions import AlreadyExistsError, ConflictError, NotFoundError
from app.core.permissions import AuthContext, OrgRoleName
from app.db.models.skill import Skill
from app.schemas.auto_skill_learning import LearnSkillCreate
from app.services.auto_skill_learning import AutoSkillLearningService

pytestmark = pytest.mark.anyio

NEW_TEXT = "# Refunds\n\nCheck the order first, then the tracking number."
BASE_TEXT = "# Refunds\n\nCheck the order first."
OTHER_TEXT = "# Refunds\n\nRefund everything, no questions asked."


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _ctx() -> AuthContext:
    return AuthContext(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role=OrgRoleName.OWNER)


def _row(name: str, content: str, *, version: int = 1, category: str | None = None) -> Skill:
    return Skill(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name=name,
        description="How refunds are handled.",
        content=content,
        category=category,
        version=version,
    )


def _proposal(**overrides: object) -> LearnSkillCreate:
    values: dict[str, object] = {
        "proposal_id": uuid.uuid4(),
        "content": NEW_TEXT,
        "rationale": "Refunds kept failing when tracking was missing.",
        "ledger_entry_id": uuid.uuid4(),
        "pattern_key": "failed:ModelHTTPError:400",
        "base_content_sha256": _sha256(BASE_TEXT),
    }
    values.update(overrides)
    return LearnSkillCreate(**values)  # type: ignore[arg-type]


class _Registry:
    """An agent the caller may view, or none at all."""

    def __init__(self, *, found: bool = True) -> None:
        self.found = found
        self.calls: list[UUID] = []

    async def get(self, ctx: AuthContext, agent_id: UUID, **_kwargs: object) -> object:
        self.calls.append(agent_id)
        if not self.found:
            raise NotFoundError(message="Agent not found", details={"agent_id": str(agent_id)})
        return object()


class _Skills:
    """The one skill writer, faked: create and update behave like the real one."""

    def __init__(self, skills: list[Skill] | None = None) -> None:
        self.by_id: dict[UUID, Skill] = {skill.id: skill for skill in skills or []}
        self.created: list[Skill] = []
        self.updated: list[Skill] = []

    async def get(self, ctx: AuthContext, skill_id: UUID, **_kwargs: object) -> Skill:
        skill = self.by_id.get(skill_id)
        if skill is None:
            raise NotFoundError(message="Skill not found", details={"skill_id": str(skill_id)})
        return skill

    async def create(self, ctx: AuthContext, **kwargs: object) -> Skill:
        skill = _row(
            str(kwargs["name"]),
            str(kwargs["content"]),
            category=kwargs.get("category"),  # type: ignore[arg-type]
        )
        skill.description = str(kwargs["description"])
        self.by_id[skill.id] = skill
        self.created.append(skill)
        return skill

    async def update(self, ctx: AuthContext, skill_id: UUID, data: object) -> Skill:
        skill = self.by_id[skill_id]
        skill.content = str(data.content)
        skill.version += 1
        self.updated.append(skill)
        return skill


@pytest.fixture
def _service(monkeypatch: pytest.MonkeyPatch) -> AutoSkillLearningService:
    service = AutoSkillLearningService.__new__(AutoSkillLearningService)
    service.db = AsyncMock()
    service.registry = _Registry()
    service.skills = _Skills()
    audit = AsyncMock()
    monkeypatch.setattr("app.services.auto_skill_learning.record_audit", audit)
    service.audit = audit  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "app.services.auto_skill_learning.skill_repo.get_by_name",
        AsyncMock(return_value=None),
    )
    return service


async def test_learning_a_new_skill_creates_it_in_the_learned_category(
    _service: AutoSkillLearningService,
) -> None:
    ctx = _ctx()
    read = await _service.learn(
        ctx, uuid.uuid4(), _proposal(name="refund-tracking", description="Check tracking first.")
    )

    assert read.created is True
    assert read.already_learned is False
    assert read.version == 1
    assert read.name == "refund-tracking"
    assert read.content_sha256 == _sha256(NEW_TEXT)
    created = _service.skills.created  # type: ignore[attr-defined]
    assert len(created) == 1
    assert created[0].category == "learned"
    audit = _service.audit  # type: ignore[attr-defined]
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "skill.learned"
    details = audit.await_args.kwargs["details"]
    assert details["pattern_key"] == "failed:ModelHTTPError:400"
    assert details["created"] is True


async def test_a_name_is_derived_from_the_pattern_key_when_the_proposal_has_none(
    _service: AutoSkillLearningService,
) -> None:
    read = await _service.learn(_ctx(), uuid.uuid4(), _proposal())

    assert read.name == "learned-failed-modelhttperror-400"
    assert _service.skills.created[0].name == "learned-failed-modelhttperror-400"  # type: ignore[attr-defined]


async def test_a_description_is_derived_from_the_rationale(
    _service: AutoSkillLearningService,
) -> None:
    await _service.learn(_ctx(), uuid.uuid4(), _proposal())

    assert (
        _service.skills.created[0].description  # type: ignore[attr-defined]
        == "Refunds kept failing when tracking was missing."
    )


async def test_learning_the_same_new_skill_twice_writes_nothing_the_second_time(
    _service: AutoSkillLearningService, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx()
    agent_id = uuid.uuid4()
    data = _proposal(name="refund-tracking")
    first = await _service.learn(ctx, agent_id, data)
    learned = first.skill_id

    existing = _service.skills.by_id[learned]  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "app.services.auto_skill_learning.skill_repo.get_by_name",
        AsyncMock(return_value=existing),
    )
    _service.audit.reset_mock()  # type: ignore[attr-defined]

    second = await _service.learn(ctx, agent_id, data)

    assert second.already_learned is True
    assert second.created is False
    assert second.skill_id == learned
    assert len(_service.skills.created) == 1  # type: ignore[attr-defined]
    _service.audit.assert_not_awaited()  # type: ignore[attr-defined]


async def test_a_name_taken_by_different_content_is_refused(
    _service: AutoSkillLearningService, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _row("refund-tracking", OTHER_TEXT)
    monkeypatch.setattr(
        "app.services.auto_skill_learning.skill_repo.get_by_name",
        AsyncMock(return_value=other),
    )

    with pytest.raises(AlreadyExistsError):
        await _service.learn(_ctx(), uuid.uuid4(), _proposal(name="refund-tracking"))

    assert _service.skills.created == []  # type: ignore[attr-defined]


async def test_an_existing_skill_already_carrying_the_text_is_a_no_op(
    _service: AutoSkillLearningService,
) -> None:
    skill = _row("refunds", NEW_TEXT, version=3)
    _service.skills = _Skills([skill])  # type: ignore[attr-defined]

    read = await _service.learn(
        _ctx(), uuid.uuid4(), _proposal(skill_id=skill.id, base_content_sha256=_sha256(BASE_TEXT))
    )

    assert read.already_learned is True
    assert read.created is False
    assert read.version == 3
    assert _service.skills.updated == []  # type: ignore[attr-defined]
    _service.audit.assert_not_awaited()  # type: ignore[attr-defined]


async def test_an_existing_skill_still_at_its_base_is_updated_through_the_skill_writer(
    _service: AutoSkillLearningService,
) -> None:
    skill = _row("refunds", BASE_TEXT, version=2)
    _service.skills = _Skills([skill])  # type: ignore[attr-defined]

    read = await _service.learn(
        _ctx(), uuid.uuid4(), _proposal(skill_id=skill.id, base_content_sha256=_sha256(BASE_TEXT))
    )

    assert read.already_learned is False
    assert read.created is False
    assert read.version == 3
    assert skill.content == NEW_TEXT
    _service.audit.assert_awaited_once()  # type: ignore[attr-defined]
    assert _service.audit.await_args.kwargs["action"] == "skill.learned"  # type: ignore[attr-defined]


async def test_a_skill_rewritten_since_the_proposal_is_refused(
    _service: AutoSkillLearningService,
) -> None:
    skill = _row("refunds", OTHER_TEXT, version=4)
    _service.skills = _Skills([skill])  # type: ignore[attr-defined]

    with pytest.raises(ConflictError):
        await _service.learn(
            _ctx(),
            uuid.uuid4(),
            _proposal(skill_id=skill.id, base_content_sha256=_sha256(BASE_TEXT)),
        )

    assert skill.content == OTHER_TEXT
    assert skill.version == 4


async def test_an_agent_the_caller_cannot_reach_is_not_found(
    _service: AutoSkillLearningService,
) -> None:
    _service.registry = _Registry(found=False)  # type: ignore[attr-defined]

    with pytest.raises(NotFoundError):
        await _service.learn(_ctx(), uuid.uuid4(), _proposal())

    assert _service.skills.created == []  # type: ignore[attr-defined]
