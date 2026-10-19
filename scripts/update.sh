#!/usr/bin/env bash
# 服务器端持续更新：备份 → 拉取代码 → 重建 → 自检；失败给出回滚命令。
# 用法：./scripts/update.sh [branch]（默认 main）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
BRANCH="${1:-main}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "✗ 当前目录不是 git 仓库，无法自动更新" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "✗ 工作区有未提交改动，请先处理（避免覆盖服务器上的临时修改）" >&2
  git status --short
  exit 1
fi

PREV="$(git rev-parse --short HEAD)"
echo "当前版本：$PREV"

echo "== 1/5 备份数据 =="
./scripts/backup.sh

echo "== 2/5 拉取代码 =="
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"
NEW="$(git rev-parse --short HEAD)"
if [[ "$NEW" == "$PREV" ]]; then
  echo "已是最新版本（$NEW），仍会继续重建以确保镜像与代码一致"
fi

echo "== 3/5 构建并启动 =="
docker compose build
docker compose up -d

echo "== 4/5 数据库迁移 =="
./scripts/migrate.sh

echo "== 5/5 自检 =="
if ./scripts/verify.sh; then
  echo "✓ 更新完成：$PREV → $NEW"
else
  echo "✗ 自检失败。回滚命令：" >&2
  echo "    git checkout $PREV && docker compose up -d --build" >&2
  exit 1
fi
