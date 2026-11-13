#!/bin/sh
# Install the locked backend and frontend dependencies for agenticos.
set -eu

uv sync --directory backend --dev
cd frontend
bun install --frozen-lockfile
