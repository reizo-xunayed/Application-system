#!/bin/bash
# Start Ausbildung Outreach. The first run sets everything up (about a minute).
set -e
cd "$(dirname "$0")"

PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  echo "Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/macos/ and run this again."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First start: setting things up…"
  "$PY" -m venv .venv
fi
if ! cmp -s requirements.txt .venv/.installed-requirements; then
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
  cp requirements.txt .venv/.installed-requirements
fi

exec .venv/bin/python -W ignore::FutureWarning -m app
