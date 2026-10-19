#!/usr/bin/env bash
# 一键部署：默认拉取 CI 构建好的 GHCR 镜像并启动，不在服务器构建。
#
# 用法：
#   sudo ./scripts/quick-deploy.sh quant.example.com            # 拉最新镜像
#   sudo ./scripts/quick-deploy.sh quant.example.com <commit-sha> # 拉指定版本
#   sudo ./scripts/quick-deploy.sh quant.example.com --build    # 服务器本地构建
#
# 前置：域名 A 记录已指向本机，80/443 已放行。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DOMAIN="${1:-}"
MODE="${2:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "用法: sudo $0 <域名> [镜像tag|--build]" >&2
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
set_env "COMPOSE_FILE" "docker-compose.yml:docker-compose.prod.yml:docker-compose.postgres.yml"
# 默认关闭虚拟货币（国内服务器通常无境外出口）；有代理时手动改 true 并配置代理
if ! grep -q "^QUANT_ENABLE_CRYPTO=" "$ENV_FILE"; then
  set_env "QUANT_ENABLE_CRYPTO" "false"
fi

if [[ "$MODE" == "--build" ]]; then
  echo "== 本地构建并启动（国内源，约 3~5 分钟）=="
  docker compose up -d --build
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
    echo "  3) 服务器访问不了 GHCR：改用 ./scripts/quick-deploy.sh $DOMAIN --build"
    exit 1
  fi
  echo "== 启动（不构建）=="
  docker compose up -d --no-build
fi

echo "== 自检 =="
./scripts/verify.sh || true

echo
echo "部署完成：https://${DOMAIN}"
echo "管理员账号：$(grep -E '^QUANT_ADMIN_USERNAME=' "$ENV_FILE" | cut -d= -f2-)"
echo "管理员密码：$(grep -E '^QUANT_ADMIN_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
echo "⚠ 立刻登录修改密码、开启两步验证；并离线备份 .env（主密钥不可恢复）。"
