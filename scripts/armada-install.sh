#!/bin/sh
# Install the locked backend and frontend dependencies for agenticos.
set -eu

uv sync --directory backend --dev
cd frontend || exit 1
bun install --frozen-lockfile
