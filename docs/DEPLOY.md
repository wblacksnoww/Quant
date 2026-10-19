# 部署指南

面向"把系统跑起来"的操作文档。三种形态：

| 场景 | 命令 | 入口 |
| --- | --- | --- |
| 单机 HTTP（内网/试用） | `docker compose up -d --build` | `http://<host>:8080` |
| 生产 HTTPS（域名 + 自动证书） | 叠加 `docker-compose.prod.yml` | `https://<域名>` |
| HTTPS + PostgreSQL | 再叠加 `docker-compose.postgres.yml` | 同上 |

## 1. 前置条件

- Docker 24+；**Compose v2.24+**（生产覆盖层使用 `!reset` 指令）。
  检查：`docker compose version`。
- 单机最低 1 vCPU / 2 GB 内存 / 5 GB 磁盘；回测吃内存，多个并发回测建议 2 vCPU / 4 GB。
- 生产 HTTPS 还需要：域名已解析到服务器、80/443 对公网可达（Let's Encrypt 签发校验）。
- 基础镜像默认走 `docker.m.daocloud.io` 镜像站（国内网络）。可直连 Docker Hub 时，
  把两个 Dockerfile 里的前缀去掉即可。**daocloud 偶发 502**（2026-10 实测）：
  `deploy/frontend.Dockerfile` 已切到 `docker.1ms.run/library/node:20-alpine`
  （backend 仍用 daocloud `python:3.12-slim`，挂了同样可换 1ms.run 前缀）。

## 2. 单机部署（HTTP）

```bash
git clone <repo> quant-trading && cd quant-trading

# 生成 .env：随机主密钥 + 随机管理员口令 + 域名参数
./scripts/init-secrets.sh localhost

# 构建并启动（首次构建后端/前端镜像）
docker compose up -d --build

# 打开 http://localhost:8080
# 用户名 admin，密码见 .env 的 QUANT_ADMIN_PASSWORD
```

脚本产生的 `.env` 权限为 600，要点：

- `QUANT_MASTER_KEY`：**绝对不能丢**（见 §6）。
- `QUANT_ADMIN_PASSWORD`：首次启动创建管理员用；之后改密码请登录后在设置页操作。
- `QUANT_HTTP_PORT`：默认 8080，冲突时改这里。

启动后自检：

```bash
./scripts/verify.sh          # 容器状态 + 两个健康检查 + quant doctor
docker compose logs -f backend
```

数据源说明：默认 `akshare`（免费直连）。服务器无外网时改用离线种子：

```dotenv
QUANT_DATA_PROVIDER=csv            # 读取镜像内 data/cache/*.csv
QUANT_FALLBACK_PROVIDERS=["synthetic"]   # 仅链路验证，界面会标注 synth 来源
```

要扩大离线数据量：在本地跑 `quant fetch --symbols ... --years 5`
（或 `docker compose exec backend quant fetch ...`），产物落 `data/store/`（parquet）。

## 3. 生产部署（HTTPS + Caddy）

```bash
./scripts/init-secrets.sh quant.example.com

# 首次：先确认 DNS 已解析，再启动
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

发生什么：

- `docker-compose.prod.yml` 用 `!reset` 清掉基础层的 8080 端口映射，改为只暴露
  80/443；新增 `caddy` 服务。
- Caddy 自动申请/续期证书，反代到内部 `frontend:80`（Nginx），
  Nginx 提供静态资源并把 `/api`、`/ws` 反代到 `backend:8000`。
- 只有一个公网入口：Caddy。后端与前端只在 compose 内网可见。

验证：

```bash
curl -I https://quant.example.com/healthz     # 200 + HSTS 头
./scripts/verify.sh
```

证书排查：`docker compose logs caddy`（80/443 不通、DNS 未生效是常见原因）。

## 4. PostgreSQL（推荐用于长期运行）

```bash
# .env 里设置强口令（不要含 $，Compose 会当变量插值）
POSTGRES_PASSWORD=<强口令>

docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

想省去每次输入两个 `-f`，可以在 `.env` 里写：

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.postgres.yml
```

之后 `docker compose up -d`、`docker compose logs` 等命令都会自动带上 PostgreSQL 覆盖层
（叠加生产层时改为三个文件用 `:` 连接）。

- 数据库端口不对宿主机暴露，仅 compose 内网可达；`./scripts/verify.sh` 等脚本自动生效。
- **已有 SQLite 数据不会自动迁移**，切换到 PG 后是全新数据库：管理员会用
  `.env` 里的 `QUANT_ADMIN_USERNAME/PASSWORD` 重新创建，历史运行记录留在旧卷里。可选路径：
  1. 保留旧库离线归档（`./scripts/backup.sh`），新库从零开始；
  2. 自行用脚本导出 `runs/users/audit_logs` 等表再导入（结构见 docs/ARCHITECTURE.md §9）。
- 备份：`docker compose exec db pg_dump -U quant quant > quant.sql`
  （SQLite 方案用 `./scripts/backup.sh`）。

## 5. 配置参考

所有后端配置 `QUANT_` 前缀，环境变量或 `.env` 皆可。常用项：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `QUANT_ENV` | `production`（compose） | 生产下拒绝自动生成主密钥 |
| `QUANT_MASTER_KEY` | — | 32 字节 base64/hex 根密钥（必填） |
| `QUANT_ADMIN_USERNAME/PASSWORD` | admin / 随机 | 仅首次启动创建 |
| `QUANT_DATABASE_URL` | SQLite | `postgresql+psycopg://user:pw@db:5432/quant` |
| `POSTGRES_DB/USER/PASSWORD` | quant / quant / — | PostgreSQL 覆盖层使用 |
| `COMPOSE_FILE` | — | 写入 `docker-compose.yml:docker-compose.postgres.yml` 可让 `docker compose` 默认叠加 PG |
| `QUANT_HTTP_PORT` | 8080 | 基础 compose 的宿主机端口 |
| `QUANT_DOMAIN` | localhost | Caddy 使用的域名 |
| `QUANT_ALLOWED_ORIGINS` | 域名列表 | CORS 白名单（同源部署不生效） |
| `QUANT_DATA_PROVIDER` | akshare | `akshare` / `csv` / `synthetic` |
| `QUANT_FALLBACK_PROVIDERS` | `["csv"]` | 降级链 |
| `QUANT_DATA_DIR` | `/app/data` | 数据目录（卷挂载点） |
| `QUANT_ACCESS_TOKEN_TTL` | 900 | access 秒数 |
| `QUANT_REQUIRE_TOTP` | false | 引导开启 2FA |
| `QUANT_RATE_LIMIT_PER_MINUTE` | 120 | 每 IP 每分钟写请求上限 |
| `QUANT_MAX_CONCURRENT_BACKTESTS` | 2 | 并发回测上限（吃内存，按机器调） |

完整模板见 [`.env.example`](../.env.example)。改完 `.env` 后
`docker compose up -d` 重建受影响容器即生效。

## 6. 数据、备份与恢复

卷与文件：

| 位置 | 内容 |
| --- | --- |
| 卷 `quant-trading-data` | `/app/data`：SQLite 库、parquet 行情缓存、开发密钥文件（如果用了自动生成） |
| 卷 `quant-trading-pg` | PostgreSQL 数据目录（启用 PG 时） |
| 卷 `quant-trading-caddy-data/config` | Caddy 证书与状态 |
| `.env`（宿主机） | 主密钥、管理员口令、数据库口令 |

```bash
./scripts/backup.sh          # 导出 /app/data 为 backups/quant-data-<时间>.tar.gz（权限 600）
```

恢复：

```bash
docker compose stop backend
docker compose exec -T backend tar -xzf - -C /app < backups/quant-data-20261018-101500.tar.gz
docker compose start backend
```

**主密钥单独备份**（复制进密码管理器/离线介质）：

- 主密钥在 `.env` 的 `QUANT_MASTER_KEY`：备份 `.env` 本身；
- 主密钥在 Docker secret / 密钥文件：备份对应文件；
- 丢失主密钥后：历史加密凭据与 TOTP 种子**不可恢复**，所有用户需重新绑定 2FA。

## 7. 升级与回滚

- 镜像 tag 固定为 `quant-trading-backend:1.0.0` / `quant-trading-frontend:1.0.0`。
  升级时改 tag（`deploy` 目录或 compose 文件），再 `docker compose up -d --build`。
- 回滚：切回旧 tag 重新 `up -d`。数据库变更走 alembic（生产启动自动 upgrade）；
  回滚镜像时若新版本已执行破坏性迁移，需要先恢复备份（`./scripts/backup.sh` 的产物）。
- 修改模型后生成迁移（本地）：
  ```bash
  cd backend
  QUANT_DATABASE_URL=sqlite:////tmp/mig.db ../.venv/bin/python -m alembic revision --autogenerate -m "add xxx"
  ```
  提交生成的迁移文件；部署时启动会自动 `upgrade head`，也可手动 `./scripts/migrate.sh`。
- 前端更新只需重建 `frontend`；后端重建不影响 Caddy/Nginx（Nginx 用 Docker DNS
  动态解析后端地址，见 `deploy/nginx.conf`）。

## 8. 日常运维

```bash
docker compose ps                      # 状态（两个健康检查：backend /healthz，frontend wget）
docker compose logs -f backend         # 后端日志
docker compose exec backend quant doctor   # 环境/密钥/数据库自检
docker compose exec backend quant user create --username alice --password '...' --role user
./scripts/verify.sh                    # 一键自检
```

- 桌面端/手机端访问同一入口即可，布局自适应。
- 用户管理：管理员登录后进入"设置 → 用户"，或 `quant user` CLI。
- 审计链校验：界面"设置 → 审计"，或 `GET /api/system/audit/verify`。

## 9. 本地开发（不用 Docker）

```bash
./scripts/dev.sh
# 后端 http://127.0.0.1:8000（/docs 交互文档）
# 前端 http://localhost:5173（需本机 Node 20+；Vite 代理 /api 与 /ws）
```

只跑后端：

```bash
cd backend
QUANT_ENV=development QUANT_DATA_PROVIDER=csv ../.venv/bin/python -m quant.cli serve --reload
```

测试：`cd backend && ../.venv/bin/python -m pytest -q`。

### 9.1 Docker 内热重载（改代码免重建镜像）

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d backend
```

原理：把宿主机 `backend/quant`（连同 `backend/tests`）挂进容器 `/app/backend/quant`
（`python -m` 优先从 cwd 导入，覆盖 site-packages 安装副本），`uvicorn --reload`
监听该目录——**保存文件后端自动重启，不用 `docker compose build backend`**。

注意：

- 前端仍需重建镜像：`docker compose build frontend && docker compose up -d frontend`；
- 恢复正常模式：`docker compose up -d backend`（不加 dev 覆盖层，容器回到镜像内代码）；
- 其它 `docker compose` 命令（如 `up -d frontend`）会按基础配置**重建偏离的 backend 容器**，
  属正常现象——先把后端代码构建进镜像即可保持一致。

## 10. 故障排查

| 现象 | 处理 |
| --- | --- |
| `docker compose up` 报 `!reset` 语法错误 | Compose 版本过低，升级到 v2.24+ |
| 拉镜像超时 | 确认 Dockerfile 使用 `docker.m.daocloud.io` 前缀；或改用可达的镜像站 |
| 前端构建慢/失败 | 已配置 npmmirror；CI 可改回官方 registry（`deploy/frontend.Dockerfile`） |
| `backend` 一直 unhealthy | `docker compose logs backend`；常见是 `.env` 主密钥格式错误或数据目录权限 |
| Caddy 证书申请失败 | 检查域名解析、80/443 放行、`docker compose logs caddy` |
| 登录后没有数据/回测为空 | 数据源不可达：切 `QUANT_DATA_PROVIDER=csv` 或先 `quant fetch` |
| 8080 被占用 | 改 `.env` 的 `QUANT_HTTP_PORT` |
| PostgreSQL 容器起不来 | `POSTGRES_PASSWORD` 未设置或含 `$`；检查 `docker-compose.postgres.yml` 的 `:?` 报错 |
| 页面 401 循环 | 清除浏览器 localStorage 中的 `quant.refresh_token` 重新登录 |

## 11. 云端部署与持续更新

### 11.1 服务器要求

| 项 | 建议 |
| --- | --- |
| 配置 | 2 vCPU / 4 GB 内存 / 40 GB SSD（回测吃内存；并发回测按机器调 `QUANT_MAX_CONCURRENT_BACKTESTS`） |
| 系统 | Ubuntu 22.04/24.04，Docker 24+、Compose v2.24+ |
| 网络 | 80/443 对公网开放；**国内服务器访问 OKX 需要自备境外代理**，否则设 `QUANT_ENABLE_CRYPTO=false` |
| 域名 | 一条 A 记录指向服务器（Caddy 自动申请证书） |

### 11.2 首次部署

```bash
# 服务器
git clone <repo> quant-trading && cd quant-trading
./scripts/init-secrets.sh quant.example.com

# 建议在 .env 中补齐：
#   POSTGRES_PASSWORD=<强口令，不含 $>
#   QUANT_ENABLE_CRYPTO=false        # 无境外代理时
#   COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:docker-compose.postgres.yml

docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.postgres.yml up -d --build
./scripts/verify.sh
```

写了 `COMPOSE_FILE` 之后，日常 `docker compose up -d / logs / ps` 会自动叠加三层配置。

### 11.3 持续更新

**方式 A（推荐）：CI 构建镜像 → 服务器拉取**

1. 把仓库推到 GitHub。`.github/workflows/ci.yml` 会在 push `main` 时：跑 pytest →
   构建后端/前端镜像 → 推送 GHCR（`ghcr.io/<owner>/quant-trading-backend:<sha>`）。
2. 服务器 `.env` 指定镜像（`<sha>` 换成 CI 输出）：
   ```dotenv
   QUANT_BACKEND_IMAGE=ghcr.io/<owner>/quant-trading-backend:<sha>
   QUANT_FRONTEND_IMAGE=ghcr.io/<owner>/quant-trading-frontend:<sha>
   ```
3. 更新：
   ```bash
   docker compose pull && docker compose up -d && ./scripts/verify.sh
   ```
4. 回滚：image 变量改回上一个 sha，再 `pull && up -d`。
5. 国内服务器拉 GHCR 慢：用阿里云 ACR / 腾讯云 TCR 同步，或配 Docker 镜像加速。

**方式 B（最简单）：服务器上 git pull + 重建**

```bash
./scripts/update.sh main
```

脚本流程：检查工作区 → 备份数据 → `git pull --ff-only` → `docker compose build` →
`up -d` → `verify.sh`；自检失败会打印回滚命令。适合单人维护、更新不频繁的场景；
缺点是服务器要跑构建（国内源约 1–3 分钟），没有 CI 测试把关。

### 11.4 更新注意事项

- **只支持单实例**：纸盘/实盘会话与 WebSocket 都在进程内存里，不能多副本扩容；
  更新会有几秒中断（容器重启），建议避开交易时段。
- **依赖已锁定**：后端 `deploy/requirements.lock.txt`（从已验证镜像导出，75 个包）、
  前端 `frontend/package-lock.json`（镜像用 `npm ci` 构建）。
  升级依赖：`./scripts/lock-deps.sh --resolve` → 重建镜像 → 跑测试 →
  `./scripts/lock-deps.sh` 固化已验证版本。
- **数据库迁移**：生产启动时自动执行 alembic——空库建表；已有业务表的旧库自动
  `stamp head`；之后只应用增量迁移。更新前仍建议 `./scripts/backup.sh`。
- **不要用代码仓库里的 `.env` 覆盖服务器配置**（`.env` 已被 .gitignore，`git pull` 不会动它）。
- **备份**：`./scripts/backup.sh` + PostgreSQL `pg_dump`；主密钥单独离线保存
  （丢失后加密凭据与 TOTP 种子不可恢复）。

### 11.5 云上安全检查清单

- [ ] 只暴露 80/443；数据库/后端端口不对公网开放（prod 覆盖层已做到）
- [ ] `QUANT_ENV=production`、`QUANT_SECURE_COOKIES=true`（prod 覆盖层已设）
- [ ] `QUANT_MASTER_KEY` 来自环境变量/密钥文件，不写进镜像
- [ ] 修改默认管理员口令，开启 TOTP
- [ ] 防火墙限制 SSH 来源 IP；开启系统安全更新
- [ ] 定期调用 `GET /api/system/audit/verify` 校验审计链
- [ ] 实盘先用测试网密钥联调；真实资金使用无提现权限的 API Key
