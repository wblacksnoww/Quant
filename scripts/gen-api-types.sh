#!/usr/bin/env bash
# 由后端 OpenAPI 生成前端类型；CI 会重新生成并检查 diff（防止手写镜像漂移）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then PY="$ROOT/.venv/bin/python"; else PY="python"; fi
fi
cd "$ROOT/backend"
QUANT_ENV=test "$PY" -m quant.cli openapi --out "$ROOT/frontend/src/api/openapi.json"
cd "$ROOT/frontend"
npx openapi-typescript src/api/openapi.json -o src/api/schema.ts
