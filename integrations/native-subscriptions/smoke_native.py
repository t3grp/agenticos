"""Opt-in smoke checks for a fresh disposable native-broker container.

Run with the container's Python interpreter and NATIVE_SMOKE_DISPOSABLE=1.
The broker must already be running, its owner registry must be empty, and its
normal startup firewall must be installed. This script never starts a server,
changes firewall rules, imports credentials, or sends a model prompt. Only an
unrecognized onboarding timeout includes a bounded, redacted prompt excerpt.
The only terminal input permitted is one Enter at the theme picker. Exit 2
means a native consent screen needs manual review.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import select
import shlex
import signal
import socket
import sqlite3
import ssl
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from native_runtime import Home

DOCS_HOST = "code.claude.com"
DOCS_PATH = "/docs/en/overview"
CLI_PATH = "/usr/local/bin/claude"
EXPECTED_CLI_VERSION = "2.1.319"
PROBE_UID = 61001
OTHER_UID = 61002
OUTPUT_LIMIT = 65536
ANSI = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*?(?:\x07|\x1b\\)|[P^_].*?\x1b\\|[ -/]*[@-~])", re.DOTALL)


class SmokeFailure(Exception):
    """A fixed label and, only for unknown onboarding, redacted fixture diagnostics."""

    def __init__(self, check: str, *, diagnostics: dict[str, object] | None = None) -> None:
        super().__init__(check)
        self.diagnostics = diagnostics


def require(condition: bool, check: str) -> None:
    if not condition:
        raise SmokeFailure(check)


def process_fields(pid: int) -> dict[str, str]:
    return dict(
        line.split(":", 1)
        for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines()
        if ":" in line
    )


def container_marker() -> None:
    require(sys.platform == "linux" and Path("/.dockerenv").is_file(), "container_required")
    require(not Path("/var/run/docker.sock").exists(), "docker_socket_must_not_be_mounted")


def broker_identity() -> tuple[int, dict[str, str]]:
    container_marker()
    require(os.geteuid() == 0, "diagnostic_root_required")
    require(os.environ.get("NATIVE_SMOKE_DISPOSABLE") == "1", "disposable_opt_in_required")
    require(Path("/app/native_server.py").is_file(), "native_broker_image_required")
    require(Path("/app/native_runtime.py").is_file(), "native_runtime_required")
    candidates: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            arguments = (entry / "cmdline").read_bytes().split(b"\0")
            fields = process_fields(int(entry.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        if b"uvicorn" in arguments and b"native_server:app" in arguments and fields["Uid"].split() == ["0"] * 4:
            candidates.append(int(entry.name))
    require(len(candidates) == 1 and candidates[0] != os.getpid(), "one_running_main_broker_required")
    pid = candidates[0]
    fields = process_fields(pid)
    init_name = Path("/proc/1/comm").read_text(encoding="ascii").strip()
    require(
        pid == 1 or (fields["PPid"].strip() == "1" and init_name in {"docker-init", "tini", "dumb-init"}),
        "private_container_pid_namespace_required",
    )
    require(Path(f"/proc/{pid}/cwd").resolve() == Path("/app"), "broker_working_directory_mismatch")
    require(fields["NoNewPrivs"].strip() == "1", "main_broker_no_new_privileges")
    allowed = sum(1 << capability for capability in (0, 1, 5, 6, 7))
    for name in ("CapEff", "CapPrm", "CapBnd"):
        require(int(fields[name], 16) == allowed, f"main_broker_{name}_unexpected")
    for name in ("CapInh", "CapAmb"):
        require(int(fields[name], 16) == 0, f"main_broker_{name}_unexpected")
    require(Path("/proc/sys/net/ipv6/conf/all/disable_ipv6").read_text().strip() == "1", "ipv6_must_be_disabled")
    return pid, fields


def empty_state() -> Path:
    root = Path(os.environ.get("NATIVE_STATE_DIR", "/state")).resolve()
    registry = root / "metadata" / "owners.sqlite3"
    require(registry.is_file(), "broker_registry_missing")
    with sqlite3.connect(registry.as_uri() + "?mode=ro", uri=True) as database:
        require(
            database.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 0,
            "disposable_empty_owner_registry_required",
        )
    require(not any((root / "users").iterdir()), "existing_native_homes_refused")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = process_fields(int(entry.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        require(int(fields["Uid"].split()[0]) not in {PROBE_UID, OTHER_UID}, "smoke_uid_already_in_use")
    return root


def host_addresses() -> list[str]:
    addresses: list[str] = []
    for value in os.environ.get("NATIVE_BLOCKED_CIDRS", "").split(","):
        if not value.strip():
            continue
        network = ipaddress.ip_network(value.strip(), strict=False)
        if network.version == 4 and network.prefixlen == 32 and network.network_address.is_global:
            addresses.append(str(network.network_address))
    require(bool(addresses), "explicit_public_host_ipv4_required")
    return sorted(set(addresses))


def tcp_available(address: str, port: int) -> None:
    with socket.create_connection((address, port), timeout=3):
        pass


def firewall_counters() -> dict[str, int]:
    result = subprocess.run(
        ["iptables-save", "-c", "-t", "filter"],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=5,
    )
    text = result.stdout.decode("ascii")
    require("-A OUTPUT " in text and "-j NATIVE_USERS" in text, "native_uid_firewall_jump_missing")
    totals = {"dns": 0, "https": 0, "rejected": 0}
    for line in text.splitlines():
        match = re.fullmatch(r"\[(\d+):\d+\] -A NATIVE_USERS (.+)", line)
        if match is None:
            continue
        packets = int(match[1])
        fields = shlex.split(match[2])
        if "-j" not in fields:
            continue
        target = fields[fields.index("-j") + 1]
        if target == "REJECT":
            totals["rejected"] += packets
        elif target == "ACCEPT" and "--ctorigdstport" in fields:
            totals["dns"] += packets
        elif target == "ACCEPT" and "--dport" in fields and fields[fields.index("--dport") + 1] == "443":
            totals["https"] += packets
    require(any("-A NATIVE_USERS " in line for line in text.splitlines()), "native_firewall_chain_missing")
    return totals


def native_probe() -> dict[str, object]:
    """Only fixed public documentation traffic and synthetic local-file checks."""
    container_marker()
    require(
        os.getuid() == PROBE_UID and os.getgid() == PROBE_UID and os.getgroups() == [], "probe_identity_not_dropped"
    )
    fields = process_fields(os.getpid())
    require(fields["NoNewPrivs"].strip() == "1", "probe_no_new_privileges")
    for name in ("CapEff", "CapPrm", "CapInh", "CapAmb"):
        require(int(fields[name], 16) == 0, f"probe_{name}_not_empty")
    require(
        not any(
            name in os.environ
            for name in (
                "NATIVE_SESSION_SERVICE_KEY",
                "ANTHROPIC_API_KEY",
                "CLAUDE_CODE_OAUTH_TOKEN",
                "POSTGRES_PASSWORD",
                "AWS_SECRET_ACCESS_KEY",
            )
        ),
        "service_credentials_inherited",
    )
    config = json.loads(sys.stdin.buffer.read(4096))
    answers = socket.getaddrinfo(DOCS_HOST, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    require(
        bool(answers) and all(ipaddress.ip_address(answer[4][0]).is_global for answer in answers),
        "public_documentation_dns",
    )
    connection = http.client.HTTPSConnection(DOCS_HOST, 443, timeout=8, context=ssl.create_default_context())
    try:
        connection.request("GET", DOCS_PATH, headers={"User-Agent": "AgenticOS-native-smoke/1"})
        response = connection.getresponse()
        require(200 <= response.status < 400, "public_documentation_https")
        response.read(1024)
    finally:
        connection.close()
    targets = [
        ("127.0.0.1", 8080),
        (config["private_address"], 8080),
        *((address, 443) for address in config["host_addresses"]),
    ]
    for address, port in targets:
        try:
            with socket.create_connection((address, port), timeout=2):
                pass
        except OSError:
            continue
        raise SmokeFailure("native_private_or_host_connection_allowed")
    require(Path(config["own_file"]).read_text() == "smoke-own", "own_home_not_readable")
    for key in ("other_file", "root_file"):
        try:
            with open(config[key], "rb") as handle:
                handle.read(1)
        except PermissionError:
            continue
        raise SmokeFailure("cross_user_or_service_file_readable")
    return {
        "dns": True,
        "public_https": True,
        "loopback_blocked": True,
        "private_blocked": True,
        "host_public_blocked": True,
        "cross_user_homes": True,
        "service_environment_absent": True,
    }


def cleanup_native(home: Home, process: subprocess.Popen[bytes] | None) -> None:
    """Observe two quiet sweeps before deleting a synthetic home."""
    from native_runtime import signal_native_user

    deadline = time.monotonic() + 3
    quiet = 0
    while time.monotonic() < deadline:
        live = signal_native_user(home.uid, signal.SIGKILL)
        if process is not None:
            process.poll()
        quiet = quiet + 1 if live == 0 else 0
        if quiet >= 2:
            if process is not None:
                process.wait(timeout=0.25)
            return
        time.sleep(0.025)
    raise SmokeFailure("synthetic_native_cleanup_failed")


def native_process(home: Home, arguments: list[str], *, payload: bytes | None = None) -> tuple[bytes, bytes, int]:
    from native_runtime import native_environment, process_limits

    process = subprocess.Popen(
        arguments,
        cwd=home.path,
        env=native_environment(home),
        user=home.uid,
        group=home.uid,
        extra_groups=(),
        start_new_session=True,
        close_fds=True,
        preexec_fn=process_limits,
        stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        output, errors = process.communicate(payload, timeout=20)
        require(len(output) <= OUTPUT_LIMIT and len(errors) <= OUTPUT_LIMIT, "native_command_output_limit")
        return output, errors, process.returncode
    finally:
        cleanup_native(home, process)


def cli_metadata(home: Home) -> dict[str, object]:
    output, errors, code = native_process(home, [CLI_PATH, "--version"])
    require(b"uv_os_get_passwd" not in (output + errors).lower(), "native_cli_user_identity_failed")
    match = re.fullmatch(rb"(\d+\.\d+\.\d+)(?: \(Claude Code\))?\s*", output)
    require(code == 0 and match is not None, "native_cli_version_failed")
    version = match[1].decode("ascii")
    require(version == EXPECTED_CLI_VERSION, "native_cli_version_mismatch")
    output, _errors, code = native_process(home, [CLI_PATH, "auth", "status", "--json"])
    require(code in {0, 1}, "native_cli_auth_status_command_failed")
    data = json.loads(output)
    require(isinstance(data, dict) and data.get("loggedIn") is False, "fresh_home_must_be_signed_out")
    method = data.get("authMethod")
    plan = data.get("subscriptionType")
    return {
        "version": version,
        "logged_in": False,
        "auth_method": method if method in {"claude.ai", "api_key", "console", "third_party", "none"} else None,
        "subscription_type": plan if plan in {"free", "pro", "max", "team", "enterprise", "ultra"} else None,
    }


def onboarding_diagnostics(buffer: bytearray, *, theme_advanced: bool) -> dict[str, object]:
    """Redact before truncation; expose no raw output, OSC data, URLs or opaque tokens."""
    text = ANSI.sub(b"", buffer).decode("utf-8", errors="replace").replace("\r", "\n")
    text = "".join(character for character in text if character.isprintable() or character in "\n\t")
    text = re.sub(r"(?i)\b(?:https?|wss?)://[^\s<>\"']+|\bwww\.[^\s<>\"']+", "[url]", text)
    text = re.sub(r"(?i)\b[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[email]", text)
    text = re.sub(
        r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|password|secret)\b\s*[:=]\s*\S+",
        "[credential]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+\S+|\bsk-[A-Z0-9_-]+\b", "[credential]", text)
    text = re.sub(r"(?<![A-Za-z0-9])[A-Za-z0-9_~+/=-]{24,}(?![A-Za-z0-9])", "[opaque]", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    patterns = {
        "cursor_position": rb"\x1b\[\??6n",
        "device_status": rb"\x1b\[5n",
        "primary_device_attributes": rb"\x1b\[(?:0)?c",
        "secondary_device_attributes": rb"\x1b\[>(?:0)?c",
        "window_size": rb"\x1b\[(?:14|16|18|19)t",
        "foreground_color": rb"\x1b\]10;\?",
        "background_color": rb"\x1b\]11;\?",
        "keyboard_protocol": rb"\x1b\[\?u",
        "mode_status": rb"\x1b\[\?(?:\d+;)*\d+\$p",
    }
    return {
        "prompt_excerpt": text[-4096:],
        "captured_bytes": len(buffer),
        "theme_advanced": theme_advanced,
        "terminal_queries": {name: len(re.findall(pattern, buffer)) for name, pattern in patterns.items()},
        "terminal_query_responses_sent": 0,
    }


def observe_onboarding(home: Home) -> dict[str, object]:
    """Observe native onboarding; advance only a recognizable theme picker once."""
    import fcntl
    import termios

    from native_runtime import native_environment, terminal_limits

    master, slave = os.openpty()
    os.fchown(slave, home.uid, home.uid)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    process = None
    buffer = bytearray()
    result: dict[str, object] = {"chooser_present": False, "theme_advanced": False, "manual_consent_required": False}
    try:
        process = subprocess.Popen(
            [CLI_PATH],
            cwd=home.path,
            env=native_environment(home),
            user=home.uid,
            group=home.uid,
            extra_groups=(),
            start_new_session=True,
            close_fds=True,
            preexec_fn=terminal_limits,
            stdin=slave,
            stdout=slave,
            stderr=slave,
        )
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.2)
            if not readable:
                require(process.poll() is None, "native_onboarding_exited")
                continue
            try:
                chunk = os.read(master, 8192)
            except OSError:
                raise SmokeFailure("native_onboarding_pty_closed") from None
            require(bool(chunk), "native_onboarding_ended")
            buffer.extend(chunk)
            require(len(buffer) <= OUTPUT_LIMIT, "native_onboarding_output_limit")
            text = ANSI.sub(b"", buffer).decode("utf-8", errors="replace").casefold()
            # Native rendering can use cursor-right controls in place of spaces.
            compact = re.sub(r"\s+", "", text)
            require(
                "uv_os_get_passwd" not in text and "err_system_error" not in text, "native_cli_user_identity_failed"
            )
            require(
                not any(
                    term in compact
                    for term in (
                        "claude.ai/oauth",
                        "console.anthropic.com/oauth",
                        "platform.claude.com/oauth",
                    )
                ),
                "unexpected_oauth_flow_started",
            )
            if any(
                term in compact
                for term in (
                    "trustthisfolder",
                    "doyoutrust",
                    "accepttheterms",
                    "agreetotheterms",
                    "bycontinuing",
                )
            ):
                result["manual_consent_required"] = True
                return result
            menu = any(
                term in compact
                for term in (
                    "selectloginmethod",
                    "choosealoginmethod",
                    "choosehowtosignin",
                    "howwouldyouliketosignin",
                )
            )
            choice_labels = (
                ("claudeaccountwithsubscription", "claudeaccount", "claudesubscription"),
                ("anthropicconsole", "consoleaccount"),
                (
                    "3rd-partyplatform",
                    "third-partyplatform",
                    "thirdpartyplatform",
                    "amazonbedrock",
                    "microsoftfoundry",
                    "vertexai",
                ),
            )
            if menu and all(any(label in compact for label in labels) for labels in choice_labels):
                result["chooser_present"] = True
                return result
            theme = ("textstyle" in compact or "/theme" in compact) and "darkmode" in compact and "lightmode" in compact
            if theme and not result["theme_advanced"]:
                require(os.write(master, b"\r") == 1, "theme_input_failed")
                result["theme_advanced"] = True
        raise SmokeFailure(
            "native_onboarding_not_recognized",
            diagnostics=onboarding_diagnostics(buffer, theme_advanced=bool(result["theme_advanced"])),
        )
    finally:
        buffer.clear()
        if slave >= 0:
            os.close(slave)
        os.close(master)
        cleanup_native(home, process)


def main() -> tuple[dict[str, object], int]:
    pid, fields = broker_identity()
    state = empty_state()
    sys.path.insert(0, "/app")
    from native_runtime import Home

    script = Path(__file__).resolve()
    info = script.stat()
    require(
        info.st_uid == 0 and stat.S_ISREG(info.st_mode) and info.st_mode & 0o022 == 0,
        "root_owned_smoke_script_required",
    )
    private_address = socket.gethostbyname(socket.gethostname())
    require(
        ipaddress.ip_address(private_address).is_private and not ipaddress.ip_address(private_address).is_loopback,
        "private_container_network_required",
    )
    hosts = host_addresses()
    for address, port in [("127.0.0.1", 8080), (private_address, 8080), *((address, 443) for address in hosts)]:
        tcp_available(address, port)
    before = firewall_counters()
    with tempfile.TemporaryDirectory(prefix="native-smoke-", dir=state) as directory:
        root = Path(directory)
        root.chmod(0o711)
        homes = [Home(root / "user-one", PROBE_UID), Home(root / "user-two", OTHER_UID)]
        for home in homes:
            home.path.mkdir(mode=0o700)
            os.chown(home.path, home.uid, home.uid)
        own = homes[0].path / "own-fixture"
        other = homes[1].path / "other-fixture"
        protected = root / "service-fixture"
        for path, uid, value in (
            (own, PROBE_UID, "smoke-own"),
            (other, OTHER_UID, "smoke-other"),
            (protected, 0, "smoke-service"),
        ):
            path.write_text(value)
            path.chmod(0o600)
            os.chown(path, uid, uid)
        payload = json.dumps(
            {
                "private_address": private_address,
                "host_addresses": hosts,
                "own_file": str(own),
                "other_file": str(other),
                "root_file": str(protected),
            }
        ).encode()
        output, _errors, code = native_process(
            homes[0],
            [sys.executable, str(script), "--native-probe"],
            payload=payload,
        )
        network = json.loads(output)
        if code != 0:
            check = network.get("check") if isinstance(network, dict) else None
            require(
                isinstance(check, str) and re.fullmatch(r"[A-Za-z0-9_]{1,100}", check) is not None,
                "native_probe_failed",
            )
            raise SmokeFailure("native_probe_" + check)
        require(network.get("ok") is True, "native_probe_contract_failed")
        after = firewall_counters()
        for counter in ("dns", "https", "rejected"):
            require(after[counter] > before[counter], f"native_firewall_{counter}_not_observed")
        metadata = cli_metadata(homes[0])
        onboarding = observe_onboarding(homes[0])
    require(process_fields(pid)["CapEff"] == fields["CapEff"], "main_broker_changed_during_smoke")
    tcp_available("127.0.0.1", 8080)
    report = {
        "ok": not onboarding["manual_consent_required"],
        "main_broker_capabilities": {
            name: fields[name].strip() for name in ("CapEff", "CapPrm", "CapBnd", "NoNewPrivs")
        },
        "network_and_isolation": network["checks"],
        "native_cli": metadata,
        "native_onboarding": onboarding,
    }
    return report, 2 if onboarding["manual_consent_required"] else 0


def entry() -> int:
    try:
        if sys.argv[1:] == ["--native-probe"]:
            report, code = {"ok": True, "checks": native_probe()}, 0
        else:
            require(len(sys.argv) == 1, "unsupported_smoke_arguments")
            report, code = main()
    except SmokeFailure as error:
        report, code = {"ok": False, "check": str(error)}, 1
        if str(error) == "native_onboarding_not_recognized" and error.diagnostics is not None:
            report["native_onboarding"] = error.diagnostics
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, sqlite3.Error) as error:
        report, code = {"ok": False, "check": "smoke_runner_failed", "error_type": type(error).__name__}, 1
    print(json.dumps(report, separators=(",", ":")), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(entry())
