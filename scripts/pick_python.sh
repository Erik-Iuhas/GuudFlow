# Sourced by the scripts: picks a Python 3.11+ interpreter, so a system python3 that is older
# (Ubuntu 22.04 / WSL ships 3.10) is never used by accident.
#   source scripts/pick_python.sh; pick_python   -> prints the path, or nothing
#   venv_ok                                       -> true when ./venv runs Python 3.11+
pick_python() {
  local c v
  for c in "${GUUDFLOW_PYTHON:-}" python3.13 python3.12 python3.11 python3 python; do
    [ -n "$c" ] || continue
    c="$(command -v "$c" 2>/dev/null)" || continue
    v="$("$c" -c 'import sys; print(int(sys.version_info >= (3, 11)))' 2>/dev/null)" || continue
    [ "$v" = 1 ] && { echo "$c"; return 0; }
  done
  if command -v uv >/dev/null 2>&1; then
    uv python find '>=3.11' 2>/dev/null && return 0
  fi
  return 1
}
venv_ok() {
  [ -x venv/bin/python ] && venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null
}
