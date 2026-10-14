#!/usr/bin/env bash
# The one Python step every script uses (prepare, setup, dev, api): a working ./venv with the
# packages from requirements.txt. Safe to re-run: it does nothing when the venv is already good.
#
#   bash scripts/run.sh python-env          (VS Code task: "Python environment (venv + packages)")
#
# Behind a proxy:
#   - HTTPS_PROXY / HTTP_PROXY are used as they are.
#   - GUUDFLOW_CA_BUNDLE=<pem>   trust your company's certificate (uv also reads the system store).
#   - GUUDFLOW_PIP_INDEX=<url>   a PyPI mirror (Artifactory, Nexus, devpi).
#   - GUUDFLOW_PYTHON=<path>     an installed Python 3.11/3.12, so uv does not download one.
#   - No PyPI at all: put the "python-wheels-<os>-py3XX" artifact zip from the "Python wheels"
#     GitHub Action (or a folder of .whl files at prebuilt/wheels/) in prebuilt/. Packages then
#     install from there with no network.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source scripts/net_env.sh   # proxy, mirror and certificate settings from .env
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fail() { echo "Python env: $*" >&2; exit 1; }

# Proxy-friendly settings for uv and pip.
export UV_NATIVE_TLS="${UV_NATIVE_TLS:-1}"   # trust the system certificate store (company CAs)
if [ -n "${GUUDFLOW_PIP_INDEX:-}" ]; then
  export UV_DEFAULT_INDEX="$GUUDFLOW_PIP_INDEX" PIP_INDEX_URL="$GUUDFLOW_PIP_INDEX"
fi

# Offline wheels: an artifact zip in prebuilt/ is unpacked into prebuilt/wheels/ (when newer).
ZIP="$(ls -t prebuilt/python-wheels*.zip 2>/dev/null | head -1)"
if [ -n "$ZIP" ] && { [ ! -f prebuilt/wheels/.unpacked ] || [ "$ZIP" -nt prebuilt/wheels/.unpacked ]; }; then
  rm -rf prebuilt/wheels && mkdir -p prebuilt/wheels
  python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$ZIP" prebuilt/wheels \
    || fail "could not unpack $ZIP"
  touch prebuilt/wheels/.unpacked
  echo "Python env: wheels from $ZIP"
fi
OFFLINE=()
if ls prebuilt/wheels/*.whl prebuilt/wheels/*/*.whl >/dev/null 2>&1; then
  WH="$(dirname "$(ls prebuilt/wheels/*.whl prebuilt/wheels/*/*.whl 2>/dev/null | head -1)")"
  OFFLINE=(--no-index --find-links "$WH")
  export UV_OFFLINE=1
  echo "Python env: installing from $WH (no network)"
fi

source scripts/pick_python.sh
REQ_HASH="$(cat requirements.txt | cksum | cut -d' ' -f1)"
if venv_ok && [ "$(cat venv/.guudflow-req 2>/dev/null)" = "$REQ_HASH" ] \
   && venv/bin/python -c 'import uvicorn, fastapi' 2>/dev/null; then
  echo "Python env: ./venv is ready ($(venv/bin/python --version 2>&1))"; exit 0
fi

USE_UV=1
[ "${GUUDFLOW_NO_UV:-0}" = "1" ] && USE_UV=0
command -v uv >/dev/null 2>&1 || USE_UV=0
PY="$(pick_python || true)"
[ "$USE_UV" = 0 ] && [ "$PY" = "3.11" ] && PY=""
if [ -z "$PY" ]; then
  fail "no Python 3.11/3.12 here and no uv to fetch one. Install uv (bash scripts/run.sh install-uv) or Python 3.11, or set GUUDFLOW_PYTHON=/path/to/python3.11"
fi

if [ -d venv ] && ! venv_ok; then
  echo "Python env: ./venv is broken or not Python 3.11/3.12: rebuilding"
  rm -rf venv
fi
if [ ! -d venv ]; then
  echo "Python env: creating ./venv with $([ "$USE_UV" = 1 ] && echo uv || echo "$PY") (Python $PY)"
  if [ "$USE_UV" = 1 ]; then
    uv venv --python "$PY" venv || fail "uv could not create the venv. If it was downloading Python and the proxy blocked it, install Python 3.11 and set GUUDFLOW_PYTHON, or set UV_PYTHON_INSTALL_MIRROR to a mirror."
  else
    "$PY" -m venv venv || fail "$PY -m venv failed (Debian/Ubuntu: sudo apt install python3.11-venv)"
  fi
fi

echo "Python env: installing requirements.txt"
if [ "$USE_UV" = 1 ]; then
  uv pip install --python venv/bin/python "${OFFLINE[@]}" -r requirements.txt
else
  venv/bin/python -m pip install "${OFFLINE[@]}" -r requirements.txt
fi || fail "package install failed. Behind a proxy: set GUUDFLOW_CA_BUNDLE (company certificate) or GUUDFLOW_PIP_INDEX (a PyPI mirror), or put the python-wheels artifact zip in prebuilt/ (README: Quirks)."

venv/bin/python -c 'import uvicorn, fastapi' || fail "packages installed but uvicorn/fastapi do not import"
echo "$REQ_HASH" > venv/.guudflow-req
echo "Python env: ./venv is ready ($(venv/bin/python --version 2>&1))"
