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
APP_ID_SECRET_NAME = "AGENTICOS_ARMADA_APP_ID"
INSTALLATION_ID_SECRET_NAME = "AGENTICOS_ARMADA_APP_INSTALLATION_ID"
PRIVATE_KEY_SECRET_NAME = "AGENTICOS_ARMADA_APP_PRIVATE_KEY"
CONNECTION_ENV = "AGENTICOS_ARMADA_CI_CONNECTION_JSON"
APP_ID_ENV = "AGENTICOS_ARMADA_APP_ID"
INSTALLATION_ID_ENV = "AGENTICOS_ARMADA_APP_INSTALLATION_ID"
PRIVATE_KEY_ENV = "AGENTICOS_ARMADA_APP_PRIVATE_KEY"
WEBHOOK_SECRET_ENV = "AGENTICOS_ARMADA_APP_WEBHOOK_SECRET"
PROTOCOL = "12"
USER_AGENT = "agenticos-armada-setup/1"


def webhook_config(app_webhook_secret: str) -> dict[str, object]:
    """Build the in-memory webhook payload; callers must not log its secret field."""
    if len(app_webhook_secret.encode("utf-8")) < 16:
        raise ValueError("the webhook signing secret must be at least 16 bytes")
    return {
        "repo": REPOSITORY,
        "branches": BRANCHES,
        "pullRequests": True,
        "githubApp": {
            "appIdSecret": APP_ID_SECRET_NAME,
            "installationIdSecret": INSTALLATION_ID_SECRET_NAME,
            "privateKeySecret": PRIVATE_KEY_SECRET_NAME,
        },
        "secret": app_webhook_secret,
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
    app_id: str,
    installation_id: str,
    private_key: str,
    app_webhook_secret: str,
    opener: Callable[..., object] = urlopen,
) -> None:
    """Store the App credentials and webhook config in Armada; do not create a GitHub App or webhook."""
    if not app_id or not installation_id or not private_key:
        raise ValueError("the GitHub App ID, installation ID and private key are required")
    config = json.dumps(webhook_config(app_webhook_secret), separators=(",", ":")).encode("utf-8")
    headers = {
        "authorization": f"Bearer {bearer}",
        "armada-protocol": PROTOCOL,
        "user-agent": USER_AGENT,
    }
    requests = [
        Request(
            f"https://{origin}/secrets/{quote(APP_ID_SECRET_NAME, safe='')}",
            data=app_id.encode("utf-8"),
            headers={**headers, "content-type": "text/plain"},
            method="PUT",
        ),
        Request(
            f"https://{origin}/secrets/{quote(INSTALLATION_ID_SECRET_NAME, safe='')}",
            data=installation_id.encode("utf-8"),
            headers={**headers, "content-type": "text/plain"},
            method="PUT",
        ),
        Request(
            f"https://{origin}/secrets/{quote(PRIVATE_KEY_SECRET_NAME, safe='')}",
            data=private_key.encode("utf-8"),
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
        configure_runtime(
            origin,
            bearer,
            environ[APP_ID_ENV],
            environ[INSTALLATION_ID_ENV],
            environ[PRIVATE_KEY_ENV],
            environ[WEBHOOK_SECRET_ENV],
        )
    except KeyError as error:
        print(f"missing required environment variable: {error.args[0]}")
        return 2
    except (ValueError, HTTPError, URLError, TimeoutError, OSError) as error:
        status = f" (HTTP {error.code})" if isinstance(error, HTTPError) else ""
        print(f"Armada runtime setup failed{status}; response details and secret values were suppressed")
        return 1

    print("configured GitHub App secret names and webhook settings for agenticos; no GitHub App or webhook was created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
