#!/bin/sh
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi
exec "$PY" edubridge.py "$@"
