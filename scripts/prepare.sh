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
#   3. gets Studio                     -> the shared step (studio_ensure.sh): an archive in prebuilt/
#                                         first, else an npm build, else a download of the release
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
# Not fatal: without uv the Python step uses pip with an installed Python 3.11/3.12.
bash scripts/install_uv.sh || NEXT+=("uv could not be installed (README: Installing uv); Python packages used pip instead")

step "2/5 setup (Python packages, Chromium, opencode, .env)"
# Studio is handled in step 3, so setup skips npm here.
GUUDFLOW_SKIP_NPM=1 bash scripts/setup.sh || NEXT+=("setup reported a problem above; fix it and run Prepare again")

step "3/5 Studio"
# The shared step: an archive in prebuilt/ (tar.gz, or the GitHub artifact zip) wins and npm is
# never run; then an existing build; then npm (only when the registry answered); then a download.
SKIP=0; [ "$NPM_OK" = 1 ] || SKIP=1
if GUUDFLOW_SKIP_NPM=$SKIP bash scripts/studio_ensure.sh --build; then
  :
elif bash scripts/studio_fetch.sh; then
  note "Studio downloaded from the repo's studio-latest release into prebuilt/"
else
  note "No Studio yet: nothing in prebuilt/, npm is not usable, and the download failed."
  NEXT+=("download studio-dist.tar.gz (or the studio-dist-tar-gz artifact zip) from GitHub,"
         "put it in $ROOT/prebuilt/ and run Prepare again")
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
source scripts/studio_dev_ok.sh
if studio_dev_ok; then
  note "- run the task 'Run dev stack' and open http://localhost:3001"
else
  note "- run the task 'Run dev stack' and open http://localhost:8000 (prebuilt Studio)"
fi
note "- add your model key in Settings -> Models & providers (or ANTHROPIC_API_KEY in .env)"
