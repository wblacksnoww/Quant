#!/usr/bin/env bash
# 维护后端依赖锁文件 deploy/requirements.lock.txt。
#
# 默认：从当前后端镜像导出已验证的版本（推荐，保证与测试/运行环境一致）
#   ./scripts/lock-deps.sh
# 升级依赖：在干净环境按 pyproject 解析最新版本（之后必须重建并跑测试）
#   ./scripts/lock-deps.sh --resolve
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OUT="deploy/requirements.lock.txt"

if [[ "${1:-}" == "--resolve" ]]; then
  echo "按 pyproject 解析最新依赖（国内源）..."
  docker run --rm -v "$ROOT/backend:/src" -w /src \
    docker.m.daocloud.io/library/python:3.12-slim \
    sh -c "pip install -q --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple && pip install -q '.[postgres]' -i https://pypi.tuna.tsinghua.edu.cn/simple && pip freeze" \
    | grep -v '^quant-trading' | grep -v '^#' | sort -f > "$OUT"
else
  IMAGE="${2:-quant-trading-backend:1.0.0}"
  echo "从镜像 $IMAGE 导出已验证版本..."
  docker run --rm "$IMAGE" pip freeze \
    | grep -v '^quant-trading' | grep -v '^#' | sort -f > "$OUT"
fi

echo "✓ 已写入 $OUT（$(wc -l < "$OUT") 个包）"
echo "  下一步：docker compose build backend && cd backend && ../.venv/bin/python -m pytest -q"
