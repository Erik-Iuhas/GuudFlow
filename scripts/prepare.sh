#!/usr/bin/env bash
# Prepare this machine for GuudFlow, whatever kind it is. Safe to re-run: every step skips what
# is already done.
#
#   bash scripts/run.sh prepare            (VS Code task: "Prepare this machine")
#
# It detects the machine (Linux, macOS, WSL, and whether the folder sits on the slow Windows disk),
# whether Docker Engine can host sandboxes, and whether npm can reach its registry, then:
#   1. installs uv                     -> Python 3.11 and the packages
#   2. runs setup                      -> venv, packages, Chromium, opencode, .env, seed data
#   3. gets Studio                     -> npm build if npm works; else unpacks studio-dist.tar.gz
#                                         from the repo folder if you put one there
#   4. writes GUUDFLOW_SANDBOX=host to .env when there is no usable Docker Engine
#   5. runs doctor and says what to do next
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
step() { printf '\n\033[36m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
NEXT=()

step "What kind of machine is this?"
KIND="$(uname -s)"
if grep -qi microsoft /proc/version 2>/dev/null; then KIND="WSL"; fi
note "system: $KIND"
case "$ROOT" in
  /mnt/[a-z]/*) note "WARNING: the project is on the Windows disk ($ROOT): installs and agent starts"
                note "will be slow. Better: git clone into ~/ inside WSL and open that folder." ;;
esac
DOCKER=0
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  if docker info --format '{{.OperatingSystem}}' 2>/dev/null | grep -qi "docker desktop"; then
    note "docker: Docker Desktop (cannot host sandboxes) -> agents will run in host mode"
  else
    DOCKER=1; note "docker: Docker Engine available -> agents run in sandboxes"
  fi
else
  note "docker: not available -> agents will run in host mode (trusted work only)"
fi
NPM_OK=0
if command -v npm >/dev/null 2>&1; then
  if timeout 20 npm ping ${GUUDFLOW_NPM_REGISTRY:+--registry "$GUUDFLOW_NPM_REGISTRY"} >/dev/null 2>&1; then
    NPM_OK=1; note "npm: registry reachable"
  else
    note "npm: registry NOT reachable (proxy?) -> Studio from a prebuilt archive if available"
  fi
else
  note "npm: not installed -> Studio from a prebuilt archive if available"
fi

step "1/5 uv"
bash scripts/install_uv.sh || { echo "uv could not be installed; see README: Installing uv"; exit 1; }

step "2/5 setup (Python packages, Chromium, opencode, .env)"
# Studio is handled in step 3, so setup skips npm here.
GUUDFLOW_SKIP_NPM=1 bash scripts/setup.sh || NEXT+=("setup reported a problem above; fix it and run Prepare again")

step "3/5 Studio"
ARCHIVE="$(ls -t studio-dist*.tar.gz 2>/dev/null | head -1)"
if [ -f studio/dist/index.html ] && [ -z "$ARCHIVE" ] && [ "$NPM_OK" = 0 ]; then
  note "a Studio build is already in studio/dist"
elif [ "$NPM_OK" = 1 ] && bash scripts/studio_deps.sh && (cd studio && npm run build); then
  note "Studio built from npm packages"
elif [ -n "$ARCHIVE" ]; then
  bash scripts/studio_bundle.sh unpack "$ARCHIVE" && note "Studio installed from $ARCHIVE"
elif [ -f studio/dist/index.html ]; then
  note "npm failed; keeping the existing build in studio/dist"
else
  note "No Studio yet: npm is not usable and there is no studio-dist.tar.gz in the repo folder."
  NEXT+=("get studio-dist.tar.gz (task 'Studio: pack…' on a machine with npm, or the CI artifact),"
         "put it in $ROOT and run Prepare again")
fi

step "4/5 sandbox mode"
if [ "$DOCKER" = 0 ] && [ -f .env ] && ! grep -qE '^\s*GUUDFLOW_SANDBOX=' .env; then
  printf '\n# Set by scripts/prepare.sh: no usable Docker Engine here, so agents run on the host.\nGUUDFLOW_SANDBOX=host\n' >> .env
  note "wrote GUUDFLOW_SANDBOX=host to .env"
else
  note "unchanged"
fi
if [ -f .env ] && ! grep -qE '^\s*GUUDFLOW_API_TOKEN=.+' .env; then
  if command -v openssl >/dev/null 2>&1; then tok="$(openssl rand -hex 24)"; else tok="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"; fi
  sed -i "s/^\s*GUUDFLOW_API_TOKEN=.*$/GUUDFLOW_API_TOKEN=$tok/" .env
  grep -qE '^\s*GUUDFLOW_API_TOKEN=' .env || echo "GUUDFLOW_API_TOKEN=$tok" >> .env
  note "generated GUUDFLOW_API_TOKEN in .env (paste it in Studio, top right)"
fi

step "5/5 doctor"
bash scripts/doctor.sh || true

step "Next"
if [ ${#NEXT[@]} -gt 0 ]; then for n in "${NEXT[@]}"; do note "- $n"; done; fi
if [ -d studio/node_modules ]; then
  note "- run the task 'Run dev stack' and open http://localhost:3001"
else
  note "- run the task 'Run dev stack' and open http://localhost:8000 (prebuilt Studio)"
fi
note "- add your model key in Settings -> Models & providers (or ANTHROPIC_API_KEY in .env)"
