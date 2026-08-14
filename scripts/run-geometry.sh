#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/apps/geometry-service"
export CHI27_ROOT="${CHI27_ROOT:-$ROOT}"
export PYTHONPATH="$PWD:$ROOT/_handoff_pack/scripts${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8788 --reload
