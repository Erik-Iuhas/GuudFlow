#!/usr/bin/env bash
# Install uv (the Python package manager GuudFlow uses). Safe to re-run: does nothing if uv is there.
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if command -v uv >/dev/null 2>&1; then
    echo "uv already installed: $(uv --version)"
    exit 0
fi
command -v curl >/dev/null 2>&1 || { echo "curl not found. On Ubuntu/WSL: sudo apt install curl" >&2; exit 1; }
echo "Installing uv from https://astral.sh/uv ..."
curl -LsSf https://astral.sh/uv/install.sh | sh
echo "uv installed: $("$HOME/.local/bin/uv" --version 2>/dev/null || uv --version)"
echo "(Other terminals may need to be reopened; the GuudFlow scripts find it on their own.)"
