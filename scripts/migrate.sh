#!/usr/bin/env bash
# 手动执行数据库迁移（幂等）：
# - 已有业务表但没有版本表 → stamp head（兼容 create_all 建出的旧库）
# - 已有版本表 → upgrade head
# - 空库 → upgrade head（建表）
# 生产启动时后端会自动执行同样的逻辑；此脚本用于部署后核对/手动触发。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

docker compose exec -T backend python -c "
from quant.config import get_settings
from quant.storage.db import make_engine
from quant.storage.migrations import upgrade_database
print('migration:', upgrade_database(make_engine(get_settings().database_url)))
"
