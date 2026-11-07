"""Install the native-user firewall, then exec the single broker process."""

import os
import sys

from network_guard import install_network_guard

install_network_guard()
os.execv(
    sys.executable,
    [
        sys.executable,
        "-m",
        "uvicorn",
        "native_server:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--workers",
        "1",
        "--no-access-log",
        "--ws-max-size",
        "16384",
        "--ws-max-queue",
        "8",
    ],
)
