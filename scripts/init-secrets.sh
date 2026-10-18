#!/usr/bin/env bash
# 生成部署用 .env：随机主密钥 + 随机管理员口令 + 域名。
# 用法：./scripts/init-secrets.sh [域名] [--force]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
DOMAIN="localhost"
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -*) echo "未知参数：$arg" >&2; exit 2 ;;
    *) DOMAIN="$arg" ;;
  esac
done

if [[ -f "$ENV_FILE" && "$FORCE" -ne 1 ]]; then
  echo "✗ $ENV_FILE 已存在。若要覆盖请加 --force（会丢失旧主密钥，已加密数据将不可恢复）" >&2
  exit 1
fi

PYTHON="$(command -v python3 || command -v python || true)"
if [[ -z "$PYTHON" ]]; then
  echo "✗ 需要 python3 来生成随机密钥" >&2
  exit 1
fi

read -r MASTER ADMIN <<<"$("$PYTHON" - <<'PY'
import base64, secrets
print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(), secrets.token_urlsafe(18))
PY
)"

umask 077
cat >"$ENV_FILE" <<EOF
# 由 scripts/init-secrets.sh 于 $(date -u +%Y-%m-%dT%H:%M:%SZ) 生成
# 注意：主密钥一旦丢失，已加密的字段与 TOTP 种子无法恢复。请另行备份！

QUANT_ENV=production
QUANT_DEBUG=false
QUANT_APP_NAME=Quant Terminal

QUANT_MASTER_KEY=${MASTER}

QUANT_ADMIN_USERNAME=admin
QUANT_ADMIN_PASSWORD=${ADMIN}

QUANT_HTTP_PORT=8080
QUANT_DOMAIN=${DOMAIN}
QUANT_ALLOWED_ORIGINS=["https://${DOMAIN}","http://localhost:8080"]

QUANT_DATABASE_URL=sqlite:////app/data/quant.db
POSTGRES_DB=quant
POSTGRES_USER=quant
POSTGRES_PASSWORD=${ADMIN}

QUANT_DATA_PROVIDER=akshare
QUANT_FALLBACK_PROVIDERS=["csv"]
QUANT_DEFAULT_SYMBOLS=600519,000001,300059

QUANT_ACCESS_TOKEN_TTL=900
QUANT_REFRESH_TOKEN_TTL=604800
QUANT_LOGIN_MAX_ATTEMPTS=5
QUANT_LOGIN_LOCK_MINUTES=15
QUANT_REQUIRE_TOTP=false
QUANT_RATE_LIMIT_PER_MINUTE=120
EOF
chmod 600 "$ENV_FILE"

echo "✓ 已生成 $ENV_FILE（权限 600）"
echo "  域名：$DOMAIN"
echo "  管理员：admin（密码已写入 .env 的 QUANT_ADMIN_PASSWORD）"
echo
echo "下一步："
echo "  docker compose up -d --build"
echo "  访问 http://localhost:\${QUANT_HTTP_PORT:-8080}"
echo
echo "⚠ 请立即备份 QUANT_MASTER_KEY 与管理员密码；生产环境建议启用两步验证。"
