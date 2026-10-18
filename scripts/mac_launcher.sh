#!/bin/bash
# Shared setup and foreground launching for the double-clickable Mac commands.

set -euo pipefail
LAUNCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LAUNCH_ROOT"
export PYTHONDONTWRITEBYTECODE=1
LAUNCH_ENV_DIR="${BILIBILI_ENV_DIR:-$HOME/Library/Application Support/BilibiliDataScience/.venv}"
LAUNCH_RUNTIME_DEFAULT="$HOME/Library/Application Support/BilibiliDataScience/runtime"

runtime_has_credentials() {
  [ -s "$1/bilibili_credential.json" ] && return 0
  local entry
  for entry in "$1/accounts/"*.json; do
    [ -s "$entry" ] && return 0
  done
  return 1
}

select_runtime() {
  if [ -n "${BILIBILI_RUNTIME_DIR:-}" ]; then
    export BILIBILI_RUNTIME_DIR
    return
  fi
  # Reuse an existing main sign-in instead of silently starting as a guest.
  # Both main launchers resolve this directory the same way. Never copy secrets.
  if [ "$LAUNCH_MODE" = main ] && ! runtime_has_credentials "$LAUNCH_RUNTIME_DEFAULT" && runtime_has_credentials "$LAUNCH_ROOT/.runtime"; then
    export BILIBILI_RUNTIME_DIR="$LAUNCH_ROOT/.runtime"
  else
    export BILIBILI_RUNTIME_DIR="$LAUNCH_RUNTIME_DEFAULT"
  fi
}

launch_error() {
  echo "" >&2
  echo "Error: $*" >&2
  if [ -t 0 ]; then
    read -r -p "Press Return to close this window." _ || true
  fi
  exit 1
}

python_supported() {
  [ -x "$1" ] && "$1" -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

select_python() {
  local env_dir candidate
  if [ -d "$LAUNCH_ENV_DIR" ]; then
    candidate="$LAUNCH_ENV_DIR/bin/python"
    python_supported "$candidate" || launch_error "The local environment needs Python 3.10 or newer. Check $LAUNCH_ENV_DIR before running setup-$LAUNCH_MODE.command."
    LAUNCH_PYTHON="$candidate"
    return
  fi
  for env_dir in .venv venv; do
    if [ "${LAUNCH_SETUP:-0}" = 1 ] && [ "$LAUNCH_MODE" = node ]; then
      break
    fi
    if [ -d "$LAUNCH_ROOT/$env_dir" ]; then
      candidate="$LAUNCH_ROOT/$env_dir/bin/python"
      if python_supported "$candidate"; then
        LAUNCH_PYTHON="$candidate"
        return
      fi
    fi
  done
  for candidate in "$(command -v python3 || true)" /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if python_supported "$candidate"; then
      LAUNCH_PYTHON="$candidate"
      return
    fi
  done
  launch_error "Python 3.10 or newer is required. Install Python, then open setup-$LAUNCH_MODE.command."
}

dependencies_ready() {
  "$LAUNCH_PYTHON" -B - "$LAUNCH_MODE" <<'PY'
import importlib.util
import sys

modules = ['bilibili_api', 'httpx']
if sys.argv[1] == 'main':
    modules += ['matplotlib', 'qrcode', 'PIL']
missing = [name for name in modules if importlib.util.find_spec(name) is None]
if missing:
    print('Missing dependencies: ' + ', '.join(missing), file=sys.stderr)
    sys.exit(1)
PY
}

setup_application() {
  LAUNCH_MODE="$1"
  LAUNCH_SETUP=1
  select_runtime
  select_python
  if [ "$LAUNCH_MODE" = main ] && dependencies_ready; then
    echo "The Ascendant main environment is ready. Double-click start-main.command."
    return
  fi
  if [ ! -d "$LAUNCH_ENV_DIR" ]; then
    echo "Creating this Mac's Python environment at $LAUNCH_ENV_DIR."
    "$LAUNCH_PYTHON" -B -m venv "$LAUNCH_ENV_DIR" || launch_error "Could not create the Python environment."
    LAUNCH_PYTHON="$LAUNCH_ENV_DIR/bin/python"
  fi
  if dependencies_ready; then
    echo "The Ascendant $LAUNCH_MODE environment is ready."
  else
    local requirements="$LAUNCH_ROOT/requirements.txt"
    if [ "$LAUNCH_MODE" = node ]; then
      requirements="$LAUNCH_ROOT/requirements-node.txt"
    fi
    echo "Installing $LAUNCH_MODE dependencies without a download cache."
    "$LAUNCH_PYTHON" -B -m pip install --no-cache-dir --no-compile -r "$requirements" || launch_error "Setup failed. Read the installation error above, then run setup-$LAUNCH_MODE.command again."
    dependencies_ready || launch_error "The dependencies are incomplete."
  fi
  echo "Double-click start-$LAUNCH_MODE.command to open the app."
  if [ -t 0 ]; then
    read -r -p "Press Return to close this window." _ || true
  fi
}

launch_application() {
  LAUNCH_MODE="$1"
  shift
  local development=0
  if [ "$LAUNCH_MODE" = main-development ]; then
    LAUNCH_MODE=main
    development=1
  fi
  select_runtime
  select_python
  dependencies_ready || launch_error "Open setup-$LAUNCH_MODE.command once, then try this launcher again."
  echo "Keep this Terminal window open while using the app; press Control+C to stop."
  if [ "$LAUNCH_MODE" = main ]; then
    echo "Starting the Ascendant dashboard at http://127.0.0.1:8000 or the next free port."
    if [ "$development" = 1 ]; then
      echo "Interface changes update automatically; Python edits restart the development server."
      echo "Environment, sign-in and runtime match start-main.command."
      exec "$LAUNCH_PYTHON" -B -u -m bilibili_ds.web --host 127.0.0.1 --port 8000 --open-browser --browser chrome "$@"
    fi
    echo "Interface changes update automatically. Python restarts are disabled so connected nodes stay connected."
    exec "$LAUNCH_PYTHON" -B -u -m bilibili_ds.web --no-reload --host 127.0.0.1 --port 8000 --open-browser --browser chrome "$@"
  else
    echo "Opening the Ascendant fetching node GUI at http://127.0.0.1:8011 or the next free port."
    exec "$LAUNCH_PYTHON" -B -u -m bilibili_ds.node --open-browser --browser chrome "$@"
  fi
}
