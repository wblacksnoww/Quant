#!/usr/bin/env bash
# 一键部署：默认拉取 CI 构建的 GHCR 镜像并启动，不在服务器构建。
#
# 默认不启用内置 Caddy（适合已有 nginx / 反向代理的服务器）：
#   前端容器监听 127.0.0.1:<QUANT_HTTP_PORT>，由宿主机反代到公网。
# 需要内置 Caddy 自动 HTTPS 时加 --caddy（会占用 80/443）。
#
# 用法：
#   ./scripts/quick-deploy.sh quant.example.com                  # 默认：nginx 共存模式
#   ./scripts/quick-deploy.sh quant.example.com --caddy          # 启用内置 Caddy（80/443）
#   ./scripts/quick-deploy.sh quant.example.com --no-caddy       # 切回 nginx 共存（移除 Caddy）
#   ./scripts/quick-deploy.sh quant.example.com <commit-sha>     # 指定镜像版本
#   ./scripts/quick-deploy.sh quant.example.com --build          # 服务器本地构建
#   ./scripts/quick-deploy.sh quant.example.com --caddy --build  # 本地构建 + Caddy
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DOMAIN=""
MODE="latest"
USE_CADDY=0
for arg in "$@"; do
  case "$arg" in
    --caddy) USE_CADDY=1 ;;
    --no-caddy) USE_CADDY=-1 ;;
    --build) MODE="--build" ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    -*) echo "未知参数：$arg" >&2; exit 2 ;;
    *)
      if [[ -z "$DOMAIN" ]]; then DOMAIN="$arg"; else MODE="$arg"; fi
      ;;
  esac
done
if [[ -z "$DOMAIN" ]]; then
  echo "用法: $0 <域名> [--caddy] [--build] [镜像tag]" >&2
  exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "✗ 未安装 Docker Compose v2（需要 v2.24+）" >&2
  exit 1
fi

ENV_FILE="$ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "== 生成 .env（随机主密钥/管理员口令）=="
  ./scripts/init-secrets.sh "$DOMAIN"
  # 用 sudo 运行时 init-secrets 会以 root 创建 .env（600），普通用户随后
  # 执行 docker compose 会读不了，这里把属主交还给调用者
  if [[ "$(id -u)" -eq 0 && -n "${SUDO_USER:-}" ]]; then
    chown "$SUDO_USER:$SUDO_USER" "$ENV_FILE"
    echo "  已将 .env 属主交还给 $SUDO_USER"
  fi
fi

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

set_env "QUANT_DOMAIN" "$DOMAIN"
# 默认两层（无 Caddy）；--caddy 才叠加 prod 层。
# 已有 COMPOSE_FILE 时：只有显式 --caddy 才改，否则保留用户选择。
BASE_COMPOSE="docker-compose.yml:docker-compose.postgres.yml"
CADDY_COMPOSE="docker-compose.yml:docker-compose.prod.yml:docker-compose.postgres.yml"
if [[ "$USE_CADDY" -eq 1 ]]; then
  set_env "COMPOSE_FILE" "$CADDY_COMPOSE"
elif [[ "$USE_CADDY" -eq -1 ]]; then
  set_env "COMPOSE_FILE" "$BASE_COMPOSE"
elif ! grep -q "^COMPOSE_FILE=" "$ENV_FILE"; then
  set_env "COMPOSE_FILE" "$BASE_COMPOSE"
fi
# 外部始终是 HTTPS（Caddy 或宿主机 nginx），cookie 需要 Secure
if ! grep -q "^QUANT_SECURE_COOKIES=" "$ENV_FILE"; then
  set_env "QUANT_SECURE_COOKIES" "true"
fi
# 虚拟货币行情默认开启（与后端默认一致）；服务器无境外出口且无代理时
# 可手动改为 false，避免交易所请求超时拖慢行情页
if ! grep -q "^QUANT_ENABLE_CRYPTO=" "$ENV_FILE"; then
  set_env "QUANT_ENABLE_CRYPTO" "true"
fi

if [[ "$MODE" == "--build" ]]; then
  echo "== 本地构建并启动（国内源，约 3~5 分钟）=="
  docker compose up -d --build --remove-orphans
else
  TAG="${MODE:-latest}"
  set_env "QUANT_BACKEND_IMAGE" "ghcr.io/wblacksnoww/quant-trading-backend:${TAG}"
  set_env "QUANT_FRONTEND_IMAGE" "ghcr.io/wblacksnoww/quant-trading-frontend:${TAG}"
  echo "== 拉取应用镜像（tag=${TAG}）=="
  if ! docker compose pull; then
    echo
    echo "✗ 拉取失败。常见原因："
    echo "  1) GHCR 包是 private：docker login ghcr.io -u wblacksnoww（PAT 需 read:packages）"
    echo "  2) 海外服务器拉不动国内基础镜像：在 .env 追加"
    echo "       QUANT_POSTGRES_IMAGE=postgres:16-alpine"
    echo "       QUANT_CADDY_IMAGE=caddy:2.8-alpine"
    echo "  3) 服务器访问不了 GHCR：改用 $0 $DOMAIN --build"
    exit 1
  fi
  echo "== 启动（不构建）=="
  docker compose up -d --no-build --remove-orphans
fi

echo "== 自检 =="
./scripts/verify.sh || true

PORT="$(grep -E '^QUANT_HTTP_PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
PORT="${PORT:-8080}"
echo
echo "部署完成。"
if [[ "$USE_CADDY" -eq 1 ]]; then
  echo "  模式：内置 Caddy 自动 HTTPS（占用 80/443）"
  echo "  访问：https://${DOMAIN}"
else
  echo "  模式：nginx 共存（未启用 Caddy）"
  echo "  前端监听：http://127.0.0.1:${PORT}"
  echo "  请在你的 nginx 中把 ${DOMAIN} 反代到该端口（需带 WebSocket 头，见 docs/DEPLOY.md §11）"
fi
echo "  管理员账号：$(grep -E '^QUANT_ADMIN_USERNAME=' "$ENV_FILE" | cut -d= -f2-)"
echo "  管理员密码：$(grep -E '^QUANT_ADMIN_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
echo "⚠ 立刻登录修改密码、开启两步验证；并离线备份 .env（主密钥不可恢复）。"
