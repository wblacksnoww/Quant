#!/usr/bin/env bash
# 部署后自检：容器健康、API 健康、审计链、前端可访问。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== 容器状态 =="
docker compose ps

echo
echo "== 后端健康 =="
docker compose exec -T backend curl -fsS http://127.0.0.1:8000/healthz
echo

echo
echo "== 等待前端 =="
PORT="${QUANT_HTTP_PORT:-}"
if [[ -z "$PORT" && -f "$ROOT/.env" ]]; then
  PORT="$(grep -E '^QUANT_HTTP_PORT=' "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
fi
PORT="${PORT:-8080}"
ok=0
for _ in $(seq 1 30); do
  body="$(curl -fsS "http://127.0.0.1:${PORT}/healthz" 2>/dev/null || true)"
  if [[ "$body" == *'"status"'* && "$body" == *'"db"'* ]]; then
    echo "✓ 前端反代正常：http://127.0.0.1:${PORT} → $body"
    ok=1
    break
  fi
  sleep 2
done
if [[ "$ok" -ne 1 ]]; then
  echo "✗ 前端健康检查失败（端口 $PORT）。若占用请改 .env 的 QUANT_HTTP_PORT" >&2
  exit 1
fi

echo
echo "== 引擎与密钥自检 =="
docker compose exec -T backend quant doctor
