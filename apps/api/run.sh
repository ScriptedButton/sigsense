#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
export PYTHONPATH="$ROOT/apps/api:$ROOT/packages/ml:${PYTHONPATH:-}"
export KMP_DUPLICATE_LIB_OK=TRUE
export OMP_NUM_THREADS=1
cd "$ROOT"
exec uvicorn sigsense_api.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 --reload
