#!/usr/bin/env bash
# Synthetic backup-probe verification. RUN ONLY INSIDE A DISPOSABLE BOAT, never on a workstation.
# Installs PostgreSQL 16 and OpenSSL inside Boat when missing, then runs the unit tests and the
# synthetic harness. Uses throwaway keys and synthetic rows only. No network service, account or secret.
set -euo pipefail

if [ "${BACKUP_PROBE_BOAT:-}" != "1" ] || [ "$(uname -s)" != "Linux" ] || [ "$(id -u)" = "0" ]; then
  echo "E_DISABLED: set BACKUP_PROBE_BOAT=1 on a Linux Boat as a non-root user"
  exit 3
fi

PG_BIN="${BACKUP_PROBE_PG_BIN:-/usr/lib/postgresql/16/bin}"
if [ ! -x "$PG_BIN/initdb" ] || ! command -v openssl >/dev/null 2>&1; then
  sudo -n apt-get update -qq
  sudo -n apt-get install -y --no-install-recommends postgresql-16 openssl
fi
[ -x "$PG_BIN/initdb" ] || { echo "E_MISSING_TOOL: postgresql-16 unavailable in this Boat"; exit 4; }

cd "$(dirname "$0")/.."   # scripts/
export PYTHONDONTWRITEBYTECODE=1

echo "== unit tests"
timeout 120 python3 -m unittest backup_probe.test_backup_probe
echo "unit exit: 0"

echo "== synthetic postgres harness"
set +e
timeout 580 python3 -m backup_probe.synthetic_postgres_restore
rc=$?
set -e
echo "harness exit: $rc"
exit "$rc"
