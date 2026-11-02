#!/usr/bin/env bash
# Run gppcc. Creates the virtualenv and installs dependencies on first run
# (and again whenever requirements.txt changes). Extra args are passed through:
#   ./run.sh --port 9000 --no-browser --profile game
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
STAMP="$VENV/.requirements.stamp"

if [ ! -x "$VENV/bin/python" ]; then
  PY="$(command -v python3 || command -v python || true)"
  if [ -z "$PY" ]; then
    echo "error: python3 not found. Install Python 3.9+ first." >&2
    exit 1
  fi
  if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    echo "error: Python 3.9+ required (found $("$PY" --version 2>&1))." >&2
    exit 1
  fi
  echo "Creating virtualenv in $VENV ..."
  "$PY" -m venv "$VENV"
fi

if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "Installing dependencies ..."
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet -r requirements.txt
  touch "$STAMP"
fi

exec "$VENV/bin/python" -m gppcc "$@"
