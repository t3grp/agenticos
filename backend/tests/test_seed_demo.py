"""The demo seed: its content, its idempotency, and what it refuses to fake.

The seed exists so the guided first-run wizard has a real workspace to show
before the user's own first run lands. What matters is that it creates exactly
the curated content, never twice, that a second run or ``--clear`` leaves
nothing half-built behind, and that the stored answer can never pass for a
real completed run: it carries no ``run_id`` and ``model_name="demo"``.
"""

import json
import uuid
from contextlib import ExitStack, asynccontextmanager, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from app.commands.seed_demo import (
    DEMO_ASSISTANT_REPLY,
    DEMO_CONVERSATION_TITLE,
    DEMO_PROJECT_NAME,
    DEMO_USER_PROMPT,
    _run,
    seed_demo,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ORG_ID = uuid.uuid4()
OWNER_ID = uuid.uuid4()


def _db_context(db):
    @asynccontextmanager
    async def context():
        yield db

    return context


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.fixture
def db():
    mock = MagicMock()
    mock.execute = AsyncMock(return_value=_result(None))
    mock.delete = AsyncMock()
    mock.flush = AsyncMock()
    return mock


@pytest.fixture
def org():
    org = MagicMock()
    org.id = ORG_ID
    org.name = "Northwind Labs"
    return org


@pytest.fixture
def project():
    project = MagicMock()
    project.id = uuid.uuid4()
    return project


@pytest.fixture
def conversation():
    conversation = MagicMock()
    conversation.id = uuid.uuid4()
    return conversation


def _patch_all(
    db,
    org,
    *,
    existing_project=None,
    existing_conversation=None,
    created_project=None,
    created_conversation=None,
):
    """Patch every repository the seed touches; lookups answer in call order."""
    lookups = [_result(existing_project)]
    if existing_conversation is not None:
        lookups.append(_result(existing_conversation))
    db.execute = AsyncMock(side_effect=lookups)
    return [
        patch("app.commands.seed_demo.get_db_context", _db_context(db)),
        patch(
            "app.commands.seed_demo.organization_repo.get_by_id",
            new=AsyncMock(return_value=org),
        ),
        patch(
            "app.commands.seed_demo.member_repo.first_owner_id",
            new=AsyncMock(return_value=OWNER_ID),
        ),
        patch(
            "app.commands.seed_demo.project_repo.create",
            new=AsyncMock(return_value=created_project),
        ),
        patch("app.commands.seed_demo.project_repo.delete", new=AsyncMock()),
        patch(
            "app.commands.seed_demo.conversation_repo.create_conversation",
            new=AsyncMock(return_value=created_conversation),
        ),
        patch(
            "app.commands.seed_demo.conversation_repo.create_message",
            new=AsyncMock(),
        ),
    ]


@contextmanager
def _patched(db, org, **kwargs):
    """Enter every patch; yields the mocks in _patch_all order."""
    with ExitStack() as stack:
        yield [stack.enter_context(p) for p in _patch_all(db, org, **kwargs)]


class TestTheContent:
    def test_the_seed_and_the_wizard_show_the_same_example(self):
        """The wizard's "what a finished result looks like" panel and the seeded
        conversation must agree word for word, or the demo contradicts itself."""
        en = json.loads((REPO_ROOT / "frontend" / "messages" / "en.json").read_text())
        example = en["start"]["examples"]["meeting"]
        assert example["prompt"] == DEMO_USER_PROMPT
        assert example["result"] == DEMO_ASSISTANT_REPLY

    def test_the_demo_content_names_itself_as_demo(self):
        assert "Demo" in DEMO_PROJECT_NAME
        assert "Demo" in DEMO_CONVERSATION_TITLE


class TestTheCommand:
    @pytest.mark.anyio
    async def test_an_invalid_organization_id_is_refused(self, db, capsys):
        with patch("app.commands.seed_demo.get_db_context", _db_context(db)):
            await _run("not-a-uuid", False, False)
        assert "Not a UUID" in capsys.readouterr().out
        db.execute.assert_not_called()

    @pytest.mark.anyio
    async def test_an_unknown_organization_is_refused(self, db, capsys):
        with (
            patch("app.commands.seed_demo.get_db_context", _db_context(db)),
            patch(
                "app.commands.seed_demo.organization_repo.get_by_id",
                new=AsyncMock(return_value=None),
            ),
        ):
            await _run(str(ORG_ID), False, False)
        assert "No organization" in capsys.readouterr().out

    @pytest.mark.anyio
    async def test_a_dry_run_creates_nothing(self, db, org, capsys):
        with _patched(db, org) as mocks:
            await _run(str(ORG_ID), False, True)
        assert "[DRY RUN]" in capsys.readouterr().out
        mocks[3].assert_not_called()

    @pytest.mark.anyio
    async def test_a_second_run_creates_nothing(self, db, org, project, capsys):
        with _patched(db, org, existing_project=project) as mocks:
            await _run(str(ORG_ID), False, False)
        assert "already exists" in capsys.readouterr().out
        mocks[3].assert_not_called()

    @pytest.mark.anyio
    async def test_creating_writes_the_project_and_the_two_messages(
        self, db, org, project, conversation, capsys
    ):
        with _patched(db, org, created_project=project, created_conversation=conversation) as mocks:
            await _run(str(ORG_ID), False, False)
        create_project = mocks[3]
        create_project.assert_awaited_once()
        kwargs = create_project.await_args.kwargs
        assert kwargs["name"] == DEMO_PROJECT_NAME
        assert kwargs["organization_id"] == ORG_ID
        assert kwargs["owner_user_id"] == OWNER_ID
        assert kwargs["visibility"] == "org"
        assert kwargs["backend"] == "state"
        mocks[5].assert_awaited_once_with(
            db,
            organization_id=ORG_ID,
            user_id=OWNER_ID,
            title=DEMO_CONVERSATION_TITLE,
            project_id=project.id,
        )
        create_message = mocks[6]
        assert create_message.await_count == 2
        user_call, assistant_call = create_message.await_args_list
        assert user_call.kwargs["role"] == "user"
        assert user_call.kwargs["content"] == DEMO_USER_PROMPT
        # The stored answer must never pass for a real run: no run id, and a
        # model name that says plainly no model ran.
        assert assistant_call.kwargs["role"] == "assistant"
        assert assistant_call.kwargs["content"] == DEMO_ASSISTANT_REPLY
        assert assistant_call.kwargs["model_name"] == "demo"
        assert "run_id" not in assistant_call.kwargs
        assert "Created demo workspace" in capsys.readouterr().out

    @pytest.mark.anyio
    async def test_clear_removes_the_conversation_before_the_project(
        self, db, org, project, conversation
    ):
        with _patched(
            db, org, existing_project=project, existing_conversation=conversation
        ) as mocks:
            await _run(str(ORG_ID), True, False)
        # Deleting the project alone would only detach the chat (its project
        # foreign key is SET NULL), leaving an ordinary chat behind.
        db.delete.assert_awaited_once_with(conversation)
        mocks[4].assert_awaited_once_with(db, project)

    @pytest.mark.anyio
    async def test_clear_with_nothing_seeded_is_a_no_op(self, db, org, capsys):
        with _patched(db, org) as mocks:
            await _run(str(ORG_ID), True, False)
        assert "No demo workspace to remove" in capsys.readouterr().out
        mocks[4].assert_not_called()

    def test_the_click_command_wires_the_options(self):
        result = CliRunner().invoke(seed_demo, ["not-a-uuid"])
        assert result.exit_code == 0
        assert "Not a UUID" in result.output
