# Sourced by the scripts (run.sh, python_env, studio_deps, studio_fetch, install_opencode): the
# network settings from .env reach every tool the scripts run, like they reach the API.
#   - proxy and mirror lines in .env (HTTPS_PROXY, NO_PROXY, GUUDFLOW_PIP_INDEX, ...) are exported;
#   - GUUDFLOW_CA_BUNDLE (a Windows path like C:\certs\corp.pem works in WSL too) is trusted on top
#     of the system roots by curl, git, pip, uv, npm and node, through a combined bundle at
#     <data>/tls/ca-bundle.pem. Variables you set yourself win.
[ -n "${_GUUDFLOW_NET_ENV:-}" ] && return 0
_GUUDFLOW_NET_ENV=1

if [ -f .env ]; then
  while IFS= read -r _line || [ -n "$_line" ]; do
    _line="${_line%$'\r'}"
    case "$_line" in
      GUUDFLOW_CA_BUNDLE=*|GUUDFLOW_PIP_INDEX=*|GUUDFLOW_NPM_REGISTRY=*|GUUDFLOW_PYTHON=*|\
      GUUDFLOW_NO_UV=*|GUUDFLOW_KEEP_WINDOWS_PATH=*|GUUDFLOW_DATA_DIR=*|\
      HTTPS_PROXY=*|HTTP_PROXY=*|NO_PROXY=*|https_proxy=*|http_proxy=*|no_proxy=*)
        _k="${_line%%=*}"; _v="${_line#*=}"
        _v="${_v%\"}"; _v="${_v#\"}"; _v="${_v%\'}"; _v="${_v#\'}"
        [ -n "$_v" ] && export "$_k=$_v" ;;
    esac
  done < .env
fi

if [ -n "${GUUDFLOW_CA_BUNDLE:-}" ]; then
  case "$GUUDFLOW_CA_BUNDLE" in   # C:\x\y.pem -> /mnt/c/x/y.pem; \\wsl.localhost\Ubuntu\home\x -> /home/x
    \\\\wsl.localhost\\*|\\\\wsl\$\\*|//wsl.localhost/*|//wsl\$/*)
      _d="$(printf '%s' "$GUUDFLOW_CA_BUNDLE" | tr '\\' '/')"
      GUUDFLOW_CA_BUNDLE="/$(printf '%s' "$_d" | cut -d/ -f5-)" ;;
    [A-Za-z]:[\\/]*)
      _d="$(printf '%s' "${GUUDFLOW_CA_BUNDLE:0:1}" | tr 'A-Z' 'a-z')"
      GUUDFLOW_CA_BUNDLE="/mnt/$_d/$(printf '%s' "${GUUDFLOW_CA_BUNDLE:3}" | tr '\\' '/')" ;;
  esac
  export GUUDFLOW_CA_BUNDLE
  if [ ! -f "$GUUDFLOW_CA_BUNDLE" ]; then
    echo "WARN: GUUDFLOW_CA_BUNDLE=$GUUDFLOW_CA_BUNDLE is not a file; downloads will not trust it" >&2
  else
    _roots=""
    for _f in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do
      [ -f "$_f" ] && { _roots="$_f"; break; }
    done
    _out="${GUUDFLOW_DATA_DIR:-./data}/tls/ca-bundle.pem"
    mkdir -p "$(dirname "$_out")" && { [ -n "$_roots" ] && cat "$_roots"; echo; cat "$GUUDFLOW_CA_BUNDLE"; } > "$_out"
    _out="$(cd "$(dirname "$_out")" && pwd)/ca-bundle.pem"
    for _var in SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE PIP_CERT GIT_SSL_CAINFO NPM_CONFIG_CAFILE; do
      [ -z "${!_var:-}" ] && export "$_var=$_out"
    done
    : "${NODE_EXTRA_CA_CERTS:=$GUUDFLOW_CA_BUNDLE}"; export NODE_EXTRA_CA_CERTS
  fi
fi
unset _line _k _v _d _roots _out _f _var
