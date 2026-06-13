#!/bin/sh
set -e

# Initialize the SQLite database if needed
python - <<'PY'
from app import init_db
init_db()
PY

exec "$@"
