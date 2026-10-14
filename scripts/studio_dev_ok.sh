# Sourced by the scripts: can this machine run Studio's dev server (vite on :3001)?
# True only with a Linux/macOS npm on PATH and a complete node_modules made for this OS: a failed
# npm install leaves no vite, and one made by Windows npm (same folder seen from WSL) has
# Windows-only binaries. Otherwise the scripts use the prebuilt studio/dist on :8000.
studio_dev_ok() {
  command -v npm >/dev/null 2>&1 || return 1
  [ -x studio/node_modules/.bin/vite ] || return 1
  [ -d studio/node_modules/@esbuild/win32-x64 ] && [ ! -d studio/node_modules/@esbuild/linux-x64 ] \
    && [ ! -d studio/node_modules/@esbuild/darwin-arm64 ] && [ ! -d studio/node_modules/@esbuild/darwin-x64 ] && return 1
  return 0
}
