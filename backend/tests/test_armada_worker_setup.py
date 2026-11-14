"""AgenticOS's checked-in settings for the reusable Armada webhook runtime."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.request import Request

import pytest

SETUP_PATH = Path(__file__).parents[2] / "ci" / "armada-worker" / "setup_runtime.py"
SPEC = importlib.util.spec_from_file_location("armada_worker_setup", SETUP_PATH)
assert SPEC is not None and SPEC.loader is not None
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def test_webhook_config_scopes_ci_to_t3_and_same_repository_pr_actions() -> None:
    config = setup.webhook_config("s" * 32)

    assert config == {
        "repo": "t3grp/agenticos",
        "branches": ["t3"],
        "pullRequests": True,
        "tokenSecret": "AGENTICOS_ARMADA_CI_GITHUB_TOKEN",
        "secret": "s" * 32,
    }


def test_signing_secret_must_meet_runtime_minimum() -> None:
    with pytest.raises(ValueError, match="at least 16 bytes"):
        setup.webhook_config("too-short")


def test_setup_stores_named_pat_then_configures_armada_without_calling_github() -> None:
    requests: list[Request] = []

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def capture(request: Request, *, timeout: int) -> Response:
        assert timeout == 15
        requests.append(request)
        return Response()

    setup.configure_runtime("armada.example", "armada-bearer", "pat-value", "signing-value" * 3, capture)

    assert [(request.method, request.full_url) for request in requests] == [
        ("PUT", "https://armada.example/secrets/AGENTICOS_ARMADA_CI_GITHUB_TOKEN"),
        ("POST", "https://armada.example/webhooks/agenticos"),
    ]
    assert requests[0].data == b"pat-value"
    assert b"pat-value" not in requests[1].data
    assert b"signing-value" in requests[1].data
    assert all(request.get_header("Armada-protocol") == "12" for request in requests)
    assert all(request.get_header("User-agent") == "agenticos-armada-setup/1" for request in requests)


def test_connection_requires_https_and_does_not_accept_url_credentials() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        setup.parse_connection('{"url":"http://armada.example","token":"x"}')
    with pytest.raises(ValueError, match="HTTPS"):
        setup.parse_connection('{"url":"https://user:pass@armada.agenticos.workers.dev","token":"x"}')
    with pytest.raises(ValueError, match="workers.dev"):
        setup.parse_connection('{"url":"https://armada.example","token":"x"}')
    with pytest.raises(ValueError, match="workers.dev"):
        setup.parse_connection('{"url":"https://armada.agenticos.workers.dev/api","token":"x"}')


def test_connection_accepts_armada_workers_dev_origin() -> None:
    assert setup.parse_connection(
        '{"url":"https://armada.agenticos.workers.dev/","token":"x"}'
    ) == ("armada.agenticos.workers.dev", "x")
