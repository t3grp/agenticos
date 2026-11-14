"""Configure the existing Armada runtime for AgenticOS. This never calls GitHub."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

REPOSITORY = "t3grp/agenticos"
PROJECT = "agenticos"
BRANCHES = ["t3"]
TOKEN_SECRET_NAME = "AGENTICOS_ARMADA_CI_GITHUB_TOKEN"
CONNECTION_ENV = "AGENTICOS_ARMADA_CI_CONNECTION_JSON"
CI_TOKEN_ENV = "AGENTICOS_ARMADA_CI_GITHUB_TOKEN"
SIGNING_SECRET_ENV = "AGENTICOS_ARMADA_WEBHOOK_SIGNING_SECRET"
PROTOCOL = "12"
USER_AGENT = "agenticos-armada-setup/1"


def webhook_config(signing_secret: str) -> dict[str, object]:
    """Build the in-memory webhook payload; callers must not log its secret field."""
    if len(signing_secret.encode("utf-8")) < 16:
        raise ValueError("the webhook signing secret must be at least 16 bytes")
    return {
        "repo": REPOSITORY,
        "branches": BRANCHES,
        "pullRequests": True,
        "tokenSecret": TOKEN_SECRET_NAME,
        "secret": signing_secret,
    }


def parse_connection(raw: str) -> tuple[str, str]:
    """Return the Armada Worker origin and bearer from its private connection JSON."""
    try:
        connection = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("the Armada connection is not valid JSON") from error
    if (
        not isinstance(connection, dict)
        or not isinstance(connection.get("url"), str)
        or not isinstance(connection.get("token"), str)
        or not connection["token"]
    ):
        raise ValueError("the Armada connection must contain url and token")
    parsed = urlsplit(connection["url"])
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("the Armada connection must use a workers.dev origin") from error
    if (
        parsed.scheme != "https"
        or hostname is None
        or hostname == "workers.dev"
        or not hostname.endswith(".workers.dev")
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("the Armada connection must use an HTTPS workers.dev origin without credentials")
    return hostname, connection["token"]


def configure_runtime(
    origin: str,
    bearer: str,
    ci_token: str,
    signing_secret: str,
    opener: Callable[..., object] = urlopen,
) -> None:
    """Store the CI token and webhook config in Armada; do not create a GitHub webhook."""
    if not ci_token:
        raise ValueError("the CI token is required")
    config = json.dumps(webhook_config(signing_secret), separators=(",", ":")).encode("utf-8")
    headers = {
        "authorization": f"Bearer {bearer}",
        "armada-protocol": PROTOCOL,
        "user-agent": USER_AGENT,
    }
    requests = [
        Request(
            f"https://{origin}/secrets/{quote(TOKEN_SECRET_NAME, safe='')}",
            data=ci_token.encode("utf-8"),
            headers={**headers, "content-type": "text/plain"},
            method="PUT",
        ),
        Request(
            f"https://{origin}/webhooks/{PROJECT}",
            data=config,
            headers={**headers, "content-type": "application/json"},
            method="POST",
        ),
    ]
    for request in requests:
        with opener(request, timeout=15):
            pass


def main(environ: Mapping[str, str] = os.environ) -> int:
    try:
        origin, bearer = parse_connection(environ[CONNECTION_ENV])
        configure_runtime(origin, bearer, environ[CI_TOKEN_ENV], environ[SIGNING_SECRET_ENV])
    except KeyError as error:
        print(f"missing required environment variable: {error.args[0]}")
        return 2
    except (ValueError, HTTPError, URLError, TimeoutError, OSError) as error:
        status = f" (HTTP {error.code})" if isinstance(error, HTTPError) else ""
        print(f"Armada runtime setup failed{status}; response details and secret values were suppressed")
        return 1

    print("configured Armada secret and webhook settings for agenticos; no GitHub webhook was created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
