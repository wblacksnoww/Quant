#!/usr/bin/env bash
# 本地开发：后端热重载 + 前端 Vite（若本机有 node/npm）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "→ 创建 Python 虚拟环境并安装依赖…"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e "backend[dev]"
fi

export QUANT_ENV=development
export QUANT_DEBUG=true
export QUANT_DATA_DIR="$ROOT/data"
export QUANT_DATABASE_URL="sqlite:///$ROOT/data/quant.db"
export QUANT_DATA_PROVIDER="${QUANT_DATA_PROVIDER:-csv}"
export QUANT_FALLBACK_PROVIDERS='["synthetic"]'
export QUANT_ALLOWED_ORIGINS='["http://localhost:5173","http://127.0.0.1:5173"]'

mkdir -p "$ROOT/data/store"

cleanup() {
  [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" 2>/dev/null || true
  [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "→ 后端 http://127.0.0.1:8000（API 文档 /docs）"
(
  cd backend
  ../.venv/bin/python -m quant.cli serve --reload --host 127.0.0.1 --port 8000
) &
BACK_PID=$!

if command -v npm >/dev/null 2>&1; then
  echo "→ 前端 http://localhost:5173"
  (
    cd frontend
    [[ -d node_modules ]] || npm install
    npm run dev
  ) &
  FRONT_PID=$!
else
  echo "⚠ 未找到 npm：跳过前端开发服务器。"
  echo "  可在 Docker 中构建前端，或直接访问后端内置的 frontend/dist（若存在）。"
fi

wait
