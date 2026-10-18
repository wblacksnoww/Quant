#!/usr/bin/env bash
# 备份容器内 /app/data（SQLite、行情缓存、开发密钥）。
# 主密钥在 .env 中时不会进入备份，请单独备份 .env。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="$ROOT/backups"
mkdir -p "$OUT_DIR"

cd "$ROOT"
docker compose exec -T backend tar -czf - -C /app data >"$OUT_DIR/quant-data-$STAMP.tar.gz"
chmod 600 "$OUT_DIR/quant-data-$STAMP.tar.gz"

echo "✓ 已备份到 $OUT_DIR/quant-data-$STAMP.tar.gz"
echo "  恢复：docker compose exec -T backend tar -xzf - -C /app < backups/quant-data-$STAMP.tar.gz"
echo "⚠ 如果 QUANT_MASTER_KEY 只存在于 .env，请同时备份 .env（离线保管）。"
