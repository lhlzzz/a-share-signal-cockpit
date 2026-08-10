#!/usr/bin/env bash
set -euo pipefail

host="${XIAOGU_API_HOST:-0.0.0.0}"
port="${XIAOGU_API_PORT:-8000}"

exec python3 -m uvicorn xiaogu_api:app --host "$host" --port "$port"
