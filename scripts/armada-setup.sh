#!/bin/sh
# Install checksum-pinned runtimes for the agenticos Armada checks.
set -eu

NODE_VERSION=24.21.0
NODE_SHA256=6e1db87ef58b8819e5d5402eff1536491b18edd8eb7bee5ef7897876e88dc5ff
NODE_ARCHIVE="node-v${NODE_VERSION}-linux-x64.tar.gz"
NODE_DIR="/opt/node-v${NODE_VERSION}"

PYTHON_VERSION=3.15.0rc2+20260901
PYTHON_ARCHIVE="cpython-${PYTHON_VERSION}-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYTHON_SHA256=6615912f874e37043912aa1516fa8b77e8496e226ba14c7b2b27843b1d819904
PYTHON_DIR="/opt/python-${PYTHON_VERSION}"

UV_VERSION=0.12.7
UV_SHA256=788f18abea7c5f55d6216e4f5613fd89d4d59b631efeec117b2b07fe72f1da21
UV_ARCHIVE="uv-x86_64-unknown-linux-gnu.tar.gz"

BUN_VERSION=1.3.5
BUN_SHA256=7051d86a924aefea3e0b96213b5fd8f79c0793f9cae6534233e627e5c3db4669
BUN_ARCHIVE="bun-linux-x64.zip"
BUN_DIR="/opt/bun-v${BUN_VERSION}"

DEBIAN_FRONTEND=noninteractive apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates curl gzip tar tzdata unzip

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

curl -fsSL -o "$tmp_dir/$NODE_ARCHIVE" \
  "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_ARCHIVE}"
printf '%s  %s\n' "$NODE_SHA256" "$tmp_dir/$NODE_ARCHIVE" | sha256sum -c -
tar -xzf "$tmp_dir/$NODE_ARCHIVE" -C "$tmp_dir"
install -d /opt
rm -rf "$NODE_DIR"
mv "$tmp_dir/node-v${NODE_VERSION}-linux-x64" "$NODE_DIR"
ln -sf "$NODE_DIR/bin/node" /usr/local/bin/node
ln -sf "$NODE_DIR/bin/npm" /usr/local/bin/npm
ln -sf "$NODE_DIR/bin/npx" /usr/local/bin/npx

curl -fsSL -o "$tmp_dir/$PYTHON_ARCHIVE" \
  "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/$PYTHON_ARCHIVE"
printf '%s  %s\n' "$PYTHON_SHA256" "$tmp_dir/$PYTHON_ARCHIVE" | sha256sum -c -
tar -xzf "$tmp_dir/$PYTHON_ARCHIVE" -C "$tmp_dir"
rm -rf "$PYTHON_DIR"
mv "$tmp_dir/python/install" "$PYTHON_DIR"
ln -sf "$PYTHON_DIR/bin/python3.15" /usr/local/bin/python3.15
ln -sf /usr/local/bin/python3.15 /usr/local/bin/python3
ln -sf /usr/local/bin/python3.15 /usr/local/bin/python

curl -fsSL -o "$tmp_dir/$UV_ARCHIVE" \
  "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_ARCHIVE}"
printf '%s  %s\n' "$UV_SHA256" "$tmp_dir/$UV_ARCHIVE" | sha256sum -c -
tar -xzf "$tmp_dir/$UV_ARCHIVE" -C "$tmp_dir"
install -m 0755 "$tmp_dir/uv-x86_64-unknown-linux-gnu/uv" /usr/local/bin/uv
install -m 0755 "$tmp_dir/uv-x86_64-unknown-linux-gnu/uvx" /usr/local/bin/uvx

curl -fsSL -o "$tmp_dir/$BUN_ARCHIVE" \
  "https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/${BUN_ARCHIVE}"
printf '%s  %s\n' "$BUN_SHA256" "$tmp_dir/$BUN_ARCHIVE" | sha256sum -c -
unzip -q "$tmp_dir/$BUN_ARCHIVE" -d "$tmp_dir"
rm -rf "$BUN_DIR"
install -d "$BUN_DIR"
install -m 0755 "$tmp_dir/bun-linux-x64/bun" "$BUN_DIR/bun"
ln -sf "$BUN_DIR/bun" /usr/local/bin/bun

ln -sf /usr/share/zoneinfo/Etc/UTC /etc/localtime
printf '%s\n' Etc/UTC > /etc/timezone

node --version
npm --version
python --version
uv --version
bun --version
