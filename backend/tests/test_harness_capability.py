"""The harness capability: registered, configured, and driving a session.

The harness is never run. A workspace whose shell plays a detached harness -
it writes a recorded event stream where the command said to, and an exit code
when it is done, and answers the platform's polls from those files - stands in
for the sandbox, so the command line, the polling, the live frames, the
parsing, the booking, the session and the clean-up all run as they would.
"""

import base64
import contextlib
import json
import re
import shlex
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_ai._run_context import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from pydantic_ai.workspaces import CommandResult, Workspace, WrapperWorkspace

from app.agents.audience import RunAudience
from app.agents.capabilities import get
from app.agents.capabilities._registry import CapabilityBinding, CapabilityBuildContext
from app.agents.capabilities.budget import SpendLedger, metered_by
from app.agents.capabilities.coding_agent import _task
from app.agents.capabilities.harness import HarnessAgent, HarnessConfig
from app.agents.capabilities.harness._report import HarnessReport
from app.agents.capabilities.harness._toolset import build_harness_toolset
from app.agents.deps import AgentDeps
from app.agents.subagent_events import SubagentEvent
from app.core.secret_kinds import ApiKeySecret
from tests.workspaces import document_workspace

pytestmark = pytest.mark.anyio

GROUP = "12345"
_TASK_FILE = re.compile(r"\.agenticos/harness/[a-f0-9]+\.(?:jsonl|err|exit)")


def _claude_stream(*, result: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """A Claude Code session as `--output-format stream-json` prints it."""
    events: list[dict[str, Any]] = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": "s-1",
            "model": "claude-sonnet-5-5",
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Looking."},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"file_path": "app.py"},
                    },
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "Bash",
                        "input": {"command": "pytest -q"},
                    },
                    {
                        "type": "tool_use",
                        "id": "t3",
                        "name": "Edit",
                        "input": {"file_path": "app.py"},
                    },
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t2", "is_error": True},
                    {"type": "tool_result", "tool_use_id": "t1", "is_error": False},
                ]
            },
        },
    ]
    if result is not None:
        events.append({"type": "result", "session_id": "s-1", **result})
    return events


_CLAUDE_DONE = {
    "subtype": "success",
    "is_error": False,
    "result": "Fixed the off-by-one in app.py.",
    "modelUsage": {
        "claude-sonnet-5-5": {
            "inputTokens": 4,
            "outputTokens": 337,
            "cacheReadInputTokens": 100,
            "cacheCreationInputTokens": 50,
        },
    },
    "permission_denials": [{"tool_name": "WebFetch"}],
}


def _lines(events: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(event) for event in events)


class _Shell(WrapperWorkspace):
    """A sandbox playing a detached harness that prints a recorded stream.

    Starting it writes the events and stderr where the line says, and - unless
    it `hangs` - the exit code, and prints its process group; a poll answers
    with the bytes past its offset, base64, and - once the group is gone - the
    exit code, as the real one-line poll does.
    """

    def __init__(
        self,
        *,
        events: str = "",
        stderr: str = "",
        exit_code: int = 0,
        hangs: bool = False,
        fails_resumed: bool = False,
    ) -> None:
        super().__init__(document_workspace())
        self.events, self.stderr, self.exit_code = events, stderr, exit_code
        self.hangs, self.fails_resumed = hangs, fails_resumed
        self.commands: list[tuple[str, Mapping[str, str] | None, float | None]] = []
        self.stopped = 0

    @property
    def started(self) -> list[str]:
        """The harness lines started, in order."""
        return [command for command, _, _ in self.commands if command.startswith("setsid")]

    async def run(
        self,
        command: Any,
        *,
        shell: bool = False,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        self.commands.append((command, env, timeout))
        if command.startswith("setsid"):
            return await self._start(command)
        if "tail -c +" in command:
            return await self._poll(command)
        if f"pkill -TERM -g {GROUP}" in command:
            self.stopped += 1
        elif command.startswith("rm -f "):
            for path in shlex.split(command)[2:]:
                with contextlib.suppress(FileNotFoundError):
                    await self.remove(path)
        return CommandResult(exit_code=0, stdout="", stderr="")

    async def _start(self, command: str) -> CommandResult:
        paths = {path.rsplit(".", 1)[1]: path for path in _TASK_FILE.findall(command)}
        resumed = "--resume" in command or " resume " in command
        events = "" if resumed and self.fails_resumed else self.events
        await self.write_bytes(paths["jsonl"], events.encode())
        await self.write_bytes(paths["err"], self.stderr.encode())
        if not self.hangs:
            code = 1 if resumed and self.fails_resumed else self.exit_code
            await self.write_bytes(paths["exit"], f"{code}\n".encode())
        return CommandResult(exit_code=0, stdout=f"{GROUP}\n", stderr="")

    async def _poll(self, command: str) -> CommandResult:
        assert f"pgrep -g {GROUP}" in command
        offset = int(command.split("tail -c +", 1)[1].split(" ", 1)[0]) - 1
        events, status = (
            next(path for path in _TASK_FILE.findall(command) if path.endswith(ext))
            for ext in (".jsonl", ".exit")
        )
        data = await self._bytes(events)
        chunk = data[offset : offset + _task.CHUNK_BYTES]
        state = "running" if self.hangs else f"exited:{(await self._bytes(status)).decode()}"
        return CommandResult(
            exit_code=0,
            stdout=f"{base64.b64encode(chunk).decode()}\n{state}\n",
            stderr="",
        )

    async def _bytes(self, path: str) -> bytes:
        try:
            return await self.read_bytes(path)
        except FileNotFoundError:
            return b""


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Polls back to back: a test waits on nothing but its own events."""
    monkeypatch.setattr(_task, "POLL_SECONDS", 0)


# The conversation a test's run answers in, and the person it runs for.
THREAD = uuid4()
RUNNER = uuid4()


def _ctx(
    workspace: Workspace | None,
    *,
    frames: list[SubagentEvent] | None = None,
) -> RunContext[AgentDeps]:
    async def sink(frame: SubagentEvent) -> None:
        if frames is not None:
            frames.append(frame)

    ctx = RunContext(
        deps=AgentDeps(
            organization_id=uuid4(),
            user_id=str(RUNNER),
            conversation_id=THREAD,
            subagent_events=sink,
            audience=RunAudience(user_id=RUNNER),
        ),
        model=TestModel(),
        usage=RunUsage(),
        retry=0,
        max_retries=1,
        tool_call_id="call-1",
    )
    if workspace is not None:
        ctx.workspace = workspace
    return ctx


def _tools(harness: Any = "claude_code", **overrides: Any) -> Any:
    options: dict[str, Any] = {
        "harness": harness,
        "access": "edit",
        "model": None,
        "base_url": None,
        "api_key": "sk-test",
        "timeout_seconds": 600,
    } | overrides
    return build_harness_toolset(**options).tools


async def _call(
    workspace: Workspace | None,
    tool: str,
    prompt: str = "Fix it",
    *,
    session: str = "main",
    frames: list[SubagentEvent] | None = None,
    **kwargs: Any,
) -> str:
    ctx = _ctx(workspace, frames=frames)
    function = _tools(**kwargs)[tool].function
    if tool in ("harness_start", "harness_prompt"):
        return await function(ctx, prompt=prompt, session=session)
    if tool == "harness_stop":
        return await function(ctx, session=session)
    return await function(ctx)


class TestRegistration:
    def test_the_capability_is_registered_with_its_four_tools(self) -> None:
        definition = get("harness")
        assert definition.name == "Harness"
        assert definition.category == "analysis"
        assert [tool.id for tool in definition.tools] == [
            "harness_start",
            "harness_prompt",
            "harness_stop",
            "harness_sessions",
        ]

    def test_a_declared_description_is_the_opening_sentence_the_model_reads(
        self,
    ) -> None:
        definition = get("harness")
        built = definition.builder(
            CapabilityBuildContext(
                binding=CapabilityBinding(capability_id="harness", config={}),
                config=HarnessConfig(),
                secret=ApiKeySecret(api_key=SecretStr("sk-test-key")),
            )
        )
        assert isinstance(built, HarnessAgent)
        tools = built.get_toolset().tools
        for declared in definition.tools:
            assert tools[declared.id].function.__doc__.startswith(declared.description)


class TestConfig:
    def test_codex_has_no_read_only_mode_to_run(self) -> None:
        with pytest.raises(ValidationError, match="Codex can only run"):
            HarnessConfig(harness="codex", access="read_only")

    def test_codex_with_default_access_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="Codex can only run"):
            HarnessConfig(harness="codex")

    def test_a_model_of_the_other_harness_vendor_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="Claude Code runs Claude models"):
            HarnessConfig(harness="codex", access="edit", model="anthropic:claude-sonnet-5-5")

    def test_an_endpoint_left_from_claude_code_is_dropped_for_codex(self) -> None:
        config = HarnessConfig(harness="codex", access="edit", base_url="https://openrouter.ai/api")
        assert config.base_url is None

    def test_the_model_is_named_without_the_vendor_the_builder_shows(self) -> None:
        assert HarnessConfig(model="anthropic:claude-sonnet-5-5").model_id == "claude-sonnet-5-5"
        assert HarnessConfig().model_id is None
        assert HarnessConfig().timeout_minutes == 30


class TestInstructions:
    def test_the_instructions_name_the_session_tools(self) -> None:
        agent = HarnessAgent(harness="claude_code", access="read_only")
        instructions = agent.get_instructions()
        assert "harness_start" in instructions
        assert "harness_prompt" in instructions
        assert "harness_stop" in instructions
        assert "reads and searches the code and answers, changing nothing" in instructions


class TestASession:
    async def test_start_opens_a_session_and_a_follow_up_resumes_it(self) -> None:
        shell = _Shell(events=_lines(_claude_stream(result=_CLAUDE_DONE)))
        frames: list[SubagentEvent] = []

        raw = await _call(shell, "harness_start", frames=frames)
        report = HarnessReport.model_validate_json(raw)
        assert report.kind == "harness"
        assert report.outcome == "completed"
        assert report.answer == "Fixed the off-by-one in app.py."
        assert report.session_id == "s-1"
        assert report.files_changed == ["app.py"]
        assert report.refused == ["WebFetch"]
        assert json.loads(raw)["session"] == "main"

        raw = await _call(shell, "harness_prompt", prompt="And the tests?")
        report = HarnessReport.model_validate_json(raw)
        assert report.outcome == "completed"
        assert "--resume s-1" in shell.started[1]
        assert frames, "the person watches the harness work"

    async def test_a_prompt_that_lost_its_session_starts_over_once(self) -> None:
        shell = _Shell(events=_lines(_claude_stream(result=_CLAUDE_DONE)), fails_resumed=True)
        await _call(shell, "harness_start")
        raw = await _call(shell, "harness_prompt", prompt="Again?")
        assert HarnessReport.model_validate_json(raw).outcome == "completed"
        assert len(shell.started) == 3
        assert "--resume" not in shell.started[2]

    async def test_listing_names_the_sessions_a_conversation_has_open(self) -> None:
        shell = _Shell(events=_lines(_claude_stream(result=_CLAUDE_DONE)))
        await _call(shell, "harness_start", session="api")
        listed = json.loads(await _call(shell, "harness_sessions"))
        assert listed == {"sessions": [{"session": "api", "harness": "claude_code"}]}

    async def test_stopping_a_session_closes_it(self) -> None:
        shell = _Shell(events=_lines(_claude_stream(result=_CLAUDE_DONE)))
        await _call(shell, "harness_start")
        stopped = json.loads(await _call(shell, "harness_stop"))
        assert stopped == {"session": "main", "stopped": True, "harness": "claude_code"}
        assert json.loads(await _call(shell, "harness_stop"))["stopped"] is False
        with pytest.raises(ModelRetry, match="harness_start first"):
            await _call(shell, "harness_prompt")

    async def test_prompting_a_session_that_was_never_opened_asks_to_open_it(
        self,
    ) -> None:
        with pytest.raises(ModelRetry, match="No harness session named `api`"):
            await _call(_Shell(), "harness_prompt", session="api")

    async def test_an_agent_with_no_sandbox_is_told_to_say_so(self) -> None:
        answer = await _call(None, "harness_start")
        assert "needs a sandbox with a shell" in answer

    async def test_an_empty_prompt_or_a_missing_key_asks_again(self) -> None:
        with pytest.raises(ModelRetry, match="Say what the harness should do"):
            await _call(_Shell(), "harness_start", prompt="  ")
        with pytest.raises(ModelRetry, match="no API key"):
            await _call(_Shell(), "harness_start", api_key=None)

    async def test_a_session_name_that_is_not_a_name_asks_again(self) -> None:
        with pytest.raises(ModelRetry, match="lower case letters"):
            await _call(_Shell(), "harness_start", session="Bad Name")

    @pytest.mark.parametrize("invalid", ["../x", "a/b", ""])
    async def test_stopping_with_an_invalid_name_is_rejected(self, invalid: str) -> None:
        with pytest.raises(ModelRetry, match="lower case letters"):
            await _call(_Shell(), "harness_stop", session=invalid)

    @pytest.mark.parametrize("invalid", ["../x", "a/b", ""])
    async def test_prompting_with_an_invalid_name_is_rejected(self, invalid: str) -> None:
        with pytest.raises(ModelRetry, match="lower case letters"):
            await _call(_Shell(), "harness_prompt", session=invalid)

    async def test_a_finished_prompt_books_what_it_spent(self) -> None:
        shell = _Shell(events=_lines(_claude_stream(result=_CLAUDE_DONE)))
        ledger = SpendLedger()
        with metered_by(ledger):
            await _call(shell, "harness_start")
        assert [(entry.model_name, entry.priced) for entry in ledger.entries] == [
            ("claude-sonnet-5-5", True)
        ]
