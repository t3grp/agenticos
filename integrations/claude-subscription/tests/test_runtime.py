import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import runtime as native
from conftest import wait_for_state
from isolation import (
    Home,
    Homes,
    Owner,
    RuntimeFailure,
    native_environment,
    within_home_limit,
)
from pydantic import ValidationError
from runtime import ClaudeRuntime, authorization_url
from schemas import CodeRequest, ConnectionStatus, InferRequest

pytestmark = pytest.mark.anyio


def request_for(owner: Owner, prompt: str = "hello") -> InferRequest:
    return InferRequest(
        organization_id=owner.organization_id,
        user_id=owner.user_id,
        model="sonnet",
        prompt=prompt,
        output_schema={"type": "object"},
    )


def connected(engine: ClaudeRuntime, owner: Owner) -> Home:
    home = engine.homes.get(owner, create=True)
    assert home is not None
    (home.path / ".fake-connected").write_text("claude.ai")
    return home


@pytest.mark.parametrize(
    "text",
    [
        b"https://claude.ai/oauth/authorize?state=x\n",
        b"Paste code https://claude.ai/oauth/authorize?state=partial",
        b"Paste code https://claude.ai.evil.example/oauth/authorize?state=x\n",
        b"Paste code https://user@claude.ai/oauth/authorize?state=x\n",
        b"Paste code https://claude.ai:9443/oauth/authorize?state=x\n",
        b"Paste code https://claude.ai/other?state=x\n",
        b"Paste code https://claude.ai/oauth/authorize#bad\n",
    ],
)
def test_prompt_requires_complete_allowlisted_provider_url(text: bytes) -> None:
    assert authorization_url(text) is None


def test_prompt_handles_terminal_colors_and_link_wrapper() -> None:
    url = "https://claude.ai/oauth/authorize?state=x"
    output = f"\x1b]8;;{url}\x1b\\{url}\x1b]8;;\x1b\\\r\n\x1b[32mPaste code:\x1b[0m"
    assert authorization_url(output.encode()) == url


async def test_real_pipes_login_code_status_and_disconnect(
    native_runtime: ClaudeRuntime, owner: Owner
) -> None:
    assert not (await native_runtime.status(owner)).connected
    started = await native_runtime.start_login(owner)
    assert started.login_id
    waiting = await wait_for_state(native_runtime, owner, "awaiting_code")
    assert (
        waiting.authorization_url
        and "claude.ai/oauth/authorize" in waiting.authorization_url
    )
    assert "fake-challenge" not in repr(waiting)
    verifying = await native_runtime.submit_code(owner, started.login_id, "test-code")
    assert verifying.state == "verifying" and verifying.authorization_url is None
    signed_in = await wait_for_state(native_runtime, owner, "connected")
    assert signed_in.connected and signed_in.login_id is None
    assert not native_runtime.processes and owner not in native_runtime.active
    result = await native_runtime.disconnect(owner)
    assert not result.connected
    assert not (await native_runtime.status(owner)).connected


async def test_prompt_can_take_longer_than_one_poll(
    native_runtime: ClaudeRuntime, owner: Owner
) -> None:
    home = native_runtime.homes.get(owner, create=True)
    assert home
    (home.path / ".delay-prompt").touch()
    started = await native_runtime.start_login(owner)
    await wait_for_state(native_runtime, owner, "awaiting_code")
    assert started.login_id
    await native_runtime.cancel_login(owner, started.login_id)


async def test_cancel_before_login_task_first_runs_releases_owner(
    native_runtime: ClaudeRuntime, owner: Owner
) -> None:
    started = await native_runtime.start_login(owner)
    assert started.login_id
    result = await native_runtime.cancel_login(owner, started.login_id)
    assert result.state == "disconnected"
    assert owner not in native_runtime.active
    retried = await native_runtime.start_login(owner)
    assert retried.login_id != started.login_id


async def test_cancel_running_login_clears_process_and_rejects_stale_code(
    native_runtime: ClaudeRuntime, owner: Owner
) -> None:
    started = await native_runtime.start_login(owner)
    assert started.login_id
    await wait_for_state(native_runtime, owner, "awaiting_code")
    process = native_runtime.account(owner).login_process
    assert process
    await native_runtime.cancel_login(owner, started.login_id)
    assert process.returncode is not None
    assert not native_runtime.processes
    await native_runtime.start_login(owner)
    with pytest.raises(RuntimeFailure, match="CLAUDE_LOGIN_STALE"):
        await native_runtime.submit_code(owner, started.login_id, "test-code")


async def test_other_owner_cannot_submit_or_cancel(
    native_runtime: ClaudeRuntime, owner: Owner
) -> None:
    started = await native_runtime.start_login(owner)
    assert started.login_id
    other = Owner(owner.organization_id, uuid4())
    for operation in (
        native_runtime.submit_code(other, started.login_id, "test-code"),
        native_runtime.cancel_login(other, started.login_id),
    ):
        with pytest.raises(RuntimeFailure, match="CLAUDE_LOGIN_STALE"):
            await operation
    assert native_runtime.account(owner).status.login_id == started.login_id


async def test_rejected_code_never_reaches_public_error(
    native_runtime: ClaudeRuntime, owner: Owner
) -> None:
    started = await native_runtime.start_login(owner)
    assert started.login_id
    await wait_for_state(native_runtime, owner, "awaiting_code")
    await native_runtime.submit_code(owner, started.login_id, "private-bad-code")
    failed = await wait_for_state(native_runtime, owner, "error")
    assert failed.error_code == "CLAUDE_LOGIN_FAILED"
    assert "private-bad-code" not in failed.model_dump_json()
    assert failed.authorization_url is None and failed.login_id is None


async def test_native_inference_returns_structured_output_and_actual_usage(
    native_runtime: ClaudeRuntime, owner: Owner
) -> None:
    connected(native_runtime, owner)
    result = await native_runtime.infer(owner, request_for(owner))
    assert result.structured_output == {"text": "Answer", "tool_calls": []}
    assert result.usage.input_tokens == 12 and result.usage.output_tokens == 8
    assert (
        result.usage.cache_read_input_tokens == 3
        and result.usage.cache_creation_input_tokens == 4
    )
    assert result.model == "claude-sonnet-test"
    assert not native_runtime.processes and not native_runtime.active


async def test_api_key_authentication_is_not_a_subscription(
    native_runtime: ClaudeRuntime, owner: Owner
) -> None:
    home = connected(native_runtime, owner)
    (home.path / ".fake-connected").write_text("api_key")
    assert not (await native_runtime.status(owner)).connected
    with pytest.raises(RuntimeFailure, match="CLAUDE_SIGN_IN_REQUIRED"):
        await native_runtime.infer(owner, request_for(owner))


@pytest.mark.parametrize(
    "prompt,code",
    [("invalid", "CLAUDE_OUTPUT_INVALID"), ("error", "CLAUDE_REQUEST_FAILED")],
)
async def test_invalid_native_results_fail_without_raw_output(
    native_runtime: ClaudeRuntime, owner: Owner, prompt: str, code: str
) -> None:
    connected(native_runtime, owner)
    with pytest.raises(RuntimeFailure, match=code) as caught:
        await native_runtime.infer(owner, request_for(owner, prompt))
    assert "private provider error" not in str(caught.value)
    assert not native_runtime.active and not native_runtime.processes


async def test_stopping_model_request_reaps_its_process(
    native_runtime: ClaudeRuntime, owner: Owner
) -> None:
    connected(native_runtime, owner)
    await native_runtime.status(owner)
    task = asyncio.create_task(native_runtime.infer(owner, request_for(owner, "hang")))
    async with asyncio.timeout(3):
        while not native_runtime.processes:
            await asyncio.sleep(0.01)
    process = next(iter(native_runtime.processes))
    with pytest.raises(RuntimeFailure, match="CLAUDE_BUSY"):
        await native_runtime.disconnect(owner)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.returncode is not None
    assert not native_runtime.active and not native_runtime.processes


async def test_output_limit_stops_process(
    native_runtime: ClaudeRuntime, owner: Owner
) -> None:
    home = connected(native_runtime, owner)
    with pytest.raises(RuntimeFailure, match="CLAUDE_OUTPUT_INVALID"):
        await native_runtime.command(
            home,
            [
                "--print",
                "--tools",
                "",
                "--safe-mode",
                "--strict-mcp-config",
                "--no-session-persistence",
            ],
            payload=b"overflow",
            output_limit=100,
            timeout=3,
        )
    assert not native_runtime.processes


def test_native_environment_has_no_inherited_provider_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-inherited")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "must-not-be-inherited")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "must-not-be-inherited")
    environment = native_environment(Home(tmp_path, 20001))
    assert set(environment) == {
        "PATH",
        "HOME",
        "CLAUDE_CONFIG_DIR",
        "USER",
        "LOGNAME",
        "LANG",
        "TERM",
        "BROWSER",
        "DISABLE_AUTOUPDATER",
    }
    assert environment["CLAUDE_CONFIG_DIR"] == str(tmp_path / ".claude")


def test_home_quota_does_not_follow_symlink(tmp_path: Path) -> None:
    root = tmp_path / "home"
    root.mkdir()
    (root / "cycle").symlink_to(root, target_is_directory=True)
    assert within_home_limit(Home(root, 20001))


def test_homes_require_container_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(RuntimeError, match="container supervisor"):
        Homes(tmp_path)


@pytest.mark.parametrize(
    "code", ["", "a\nb", "a\rb", "a\x1bb", "a\x7fb", "a b", "x" * 2049]
)
def test_code_rejects_terminal_input_and_keeps_repr_private(code: str) -> None:
    with pytest.raises(ValidationError):
        CodeRequest(
            organization_id=UUID(int=1),
            user_id=UUID(int=2),
            login_id=UUID(int=3),
            code=code,
        )
    valid = CodeRequest(
        organization_id=UUID(int=1),
        user_id=UUID(int=2),
        login_id=UUID(int=3),
        code="private-code",
    )
    assert "private-code" not in repr(valid)


async def test_failed_status_process_cannot_mark_account_connected(
    native_runtime: ClaudeRuntime, owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = connected(native_runtime, owner)
    monkeypatch.setattr(
        native_runtime,
        "command",
        AsyncMock(return_value=(b'{"loggedIn":true,"authMethod":"claude.ai"}', 7)),
    )
    assert not await native_runtime.probe(home)


@pytest.mark.parametrize(
    "subtype,is_error",
    [("error_max_turns", False), ("success", True), ("success", None)],
)
async def test_partial_or_failed_native_envelope_cannot_complete_agent_step(
    native_runtime: ClaudeRuntime,
    owner: Owner,
    monkeypatch: pytest.MonkeyPatch,
    subtype: str,
    is_error: bool | None,
) -> None:
    connected(native_runtime, owner)
    account = native_runtime.account(owner)
    account.checked = True
    account.status = ConnectionStatus(connected=True, state="connected")
    payload = {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "structured_output": {"text": "Incomplete", "tool_calls": []},
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }
    monkeypatch.setattr(
        native_runtime,
        "command",
        AsyncMock(return_value=(json.dumps(payload).encode(), 0)),
    )
    with pytest.raises(RuntimeFailure, match="CLAUDE_REQUEST_FAILED"):
        await native_runtime.infer(owner, request_for(owner))
    assert owner not in native_runtime.active


@pytest.mark.parametrize("before_prompt", [True, False])
async def test_login_expiry_cleans_up_and_allows_retry(
    native_runtime: ClaudeRuntime,
    owner: Owner,
    monkeypatch: pytest.MonkeyPatch,
    before_prompt: bool,
) -> None:
    home = native_runtime.homes.get(owner, create=True)
    assert home
    if before_prompt:
        (home.path / ".delay-prompt").touch()
        monkeypatch.setattr(native, "PROMPT_SECONDS", 0.05)
    else:
        monkeypatch.setattr(native, "LOGIN_SECONDS", 0.3)
    first = await native_runtime.start_login(owner)
    failed = await wait_for_state(native_runtime, owner, "error")
    assert failed.error_code == "CLAUDE_LOGIN_TIMEOUT"
    assert failed.authorization_url is None and failed.login_id is None
    assert not native_runtime.active and not native_runtime.processes
    retry = await native_runtime.start_login(owner)
    assert retry.login_id is not None and retry.login_id != first.login_id
