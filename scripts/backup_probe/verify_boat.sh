#!/usr/bin/env bash
# Synthetic backup-probe verification. RUN ONLY INSIDE A DISPOSABLE BOAT, never on a workstation.
# Installs PostgreSQL 16 and OpenSSL inside Boat when missing, then runs the unit tests and the
# synthetic harness. Uses throwaway keys and synthetic rows only. No network service, account or secret.
#
# Time budget (Boat limit 600s): unit tests <= 60s, harness <= 400s of work (internal deadline) plus
# cleanup; the wrapper sends TERM at 450s and KILL 30s later. Total worst case is about 540s.
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
timeout -s TERM -k 10 60 python3 -m unittest backup_probe.test_backup_probe
echo "unit exit: 0"

echo "== synthetic postgres harness"
# Run in the background and forward TERM/INT so Python's own handler and finally blocks run.
pid=""
forward() { [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true; }
trap forward TERM INT
timeout -s TERM -k 30 450 python3 -m backup_probe.synthetic_postgres_restore &
pid=$!
set +e
wait "$pid"
rc=$?
if [ "$rc" -gt 128 ]; then wait "$pid"; rc=$?; fi   # a trapped signal interrupts the first wait
set -e
trap - TERM INT
echo "harness exit: $rc"

echo "== leftovers (owned by this run)"
left=0
if pgrep -u "$(id -u)" -f 'bp-synth-' >/dev/null 2>&1; then echo "LEFTOVER: process"; left=1; fi
if ls -d "${TMPDIR:-/tmp}"/bp-synth-* >/dev/null 2>&1; then echo "LEFTOVER: temp dir"; left=1; fi
[ "$left" = 0 ] && echo "leftovers: none"
if [ "$left" != 0 ] && [ "$rc" = 0 ]; then rc=5; fi
exit "$rc"
