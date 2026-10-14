# Sourced by the scripts: picks the Python for GuudFlow's venv. GuudFlow targets 3.11 (CI runs
# 3.11; some pinned packages do not build on 3.13+), so: 3.11 first, then 3.12. A system python3
# that is older (Ubuntu 22.04 / WSL ships 3.10) or newer is never used by accident.
#   source scripts/pick_python.sh; pick_python   -> prints a path or a uv request ("3.11")
#   venv_ok                                       -> true when ./venv runs Python 3.11 or 3.12
_py_minor() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null; }
pick_python() {
  local c m
  if [ -n "${GUUDFLOW_PYTHON:-}" ]; then echo "$GUUDFLOW_PYTHON"; return 0; fi
  for want in 3.11 3.12; do
    for c in "python$want" python3 python; do
      c="$(command -v "$c" 2>/dev/null)" || continue
      m="$(_py_minor "$c")" || continue
      [ "$m" = "$want" ] && { echo "$c"; return 0; }
    done
  done
  # Nothing suitable installed: let uv fetch 3.11 (it downloads a standalone build).
  command -v uv >/dev/null 2>&1 && { echo "3.11"; return 0; }
  return 1
}
venv_ok() {
  [ -x venv/bin/python ] && case "$(_py_minor venv/bin/python)" in 3.11|3.12) return 0 ;; *) return 1 ;; esac
}
