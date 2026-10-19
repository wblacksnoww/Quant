# Quant Terminal

多市场（**A股 / 美股 / 港股 / 虚拟货币 / 场外基金**）量化研究平台：
**事件驱动回测 + 模拟盘 + 实盘 + 风控 + Web 终端**。
一套撮合与会话代码同时服务回测、纸盘和实盘，差异只在时间驱动与订单去向，
避免"回测一套、实盘一套"的漂移。

> 仅用于研究与教学，不构成投资建议。**实盘功能默认关闭**，需显式开启环境变量并
> 二次确认；开启后程序会向交易所/券商发送真实委托，请自行承担交易结果与合规责任。
> A 股程序化交易需券商开通权限并按规定报备。详见 [docs/LIVE_TRADING.md](docs/LIVE_TRADING.md)。

## 特性

- **多市场行情与研究**：统一符号 `MARKET:CODE`（`CN:600519` / `US:AAPL` / `HK:00700` /
  `CRYPTO:BTC/USDT` / `FUND:007005`），日线与实时快照开箱可用；检索联想支持代码/名称并可按市场过滤；
  价格按币种显示（¥ / $ / HK$ / USDT），成交量统一以"股/币"计。
- **虚拟货币**：ccxt 对接 OKX/Binance/Gate 等交易所现货行情（交易所可配置），
  7×24 全周日历、T+0、无涨跌停、小数数量；回测/纸盘/蓝图全链路可用。
- **热门推荐与排行榜**：行情页搜索框下方按成交量推荐热门标的；排行榜支持
  A股/美股/港股/虚拟货币 × 今日/本周/本月 × 涨幅/跌幅/成交额（今日榜 5 秒刷新，
  周/月榜 30 秒；周/月为活跃榜单样本的窗口涨幅）。
- **指数卡片**：行情页顶部展示 12 个常用指数（A股/港股/美股）实时点数与涨跌，
  可编辑显示哪些、调整顺序（配置存浏览器），5 秒刷新。
- **可拖拽卡片布局**：行情页与仪表盘都是卡片网格，点「编辑布局」可拖动排序、
  切换宽/窄、隐藏卡片，布局存浏览器；刷新页面不会丢失。
- **实时行情**：自选表可选 1 秒刷新（后端快照 TTL 可配 `QUANT_QUOTE_TTL`），
  轮询为静默更新（不闪 loading）；标的详情页现价 3 秒刷新，虚拟货币直接取交易所实时价。
- **K 线周期递进**：详情页支持今日（5 分钟）/本周（30 分钟）/本月/1-5 年，
  持续缩小会自动升级到更长周期（分钟 → 日线 → 周线 → 月线），刷新时保留缩放位置。
- **标的详情与一键启动**：行情页点击代码/名称进入详情页——K 线、交易规则
  （涨跌停/整手/T+0-T+1）、策略选择与参数，直接对这只标的启动模拟盘或实盘。
- **场外基金**：申购/赎回按净值确认份额，流水聚合持仓与收益，净值曲线、费率与
  补录修正；场内 ETF 走 A 股链路，搜索带 ETF 徽标。
- **事件驱动引擎**：策略在第 t 根 K 线收到行情，订单在 t+1 撮合，从机制上杜绝未来函数。
- **同一套交易组件**：按市场取规则与成本预设——A股整手/涨跌停/T+1/佣金/印花税/过户费；
  美股 lot=1、T+0、无涨跌停、低佣金；港股每手 100 股、T+0、双边印花；
  虚拟货币小数数量、T+0、无最低佣金。回测、纸盘与实盘完全一致。
- **实盘对接**：`LiveBroker + BrokerAdapter`——ccxt（虚拟货币，测试网可验证）与
  miniQMT（A股骨架，需 Windows + QMT 权限）。默认测试网、真实资金二次确认、
  服务端总开关三道闸门；启动对账、逐单撤单、全链路审计。
- **模拟盘双模式**：`replay` 按倍速回放历史行情；`live` 盘中轮询快照（数据源不支持时回退最新收盘价并告警）。**每个账号一份独立会话**，同一账号同时只能开一个。
- **实时行情自选**：行情页自选列表实时快照（腾讯 → 东财 → 新浪 多源聚合，单源限流自动切换），3/5/10/30 秒自动刷新可暂停；代码/名称搜索联想加入自选（带市场徽标），单源全挂时回退最近一次成功快照并标注。
- **策略可插拔**：`ParamSpec` 自描述参数，前端自动渲染表单；新增策略无需改 API 或前端。
  另有「条件拼装策略」：网页下拉框拼买卖规则（spec v1）或 **蓝图编辑器**拖节点连线
  （spec v2 节点数据流图，React Flow 画布 + 三模板），免代码创建、存库、直接回测/纸盘。
- **多用户**：`admin` / `user` 角色；管理页创建账号、调整角色、停用与重置密码；回测、纸盘会话与加密凭据按用户隔离（WebSocket 事件也按用户投递），管理员可查看全部记录。
- **风控前置**：单票仓位、单笔金额、持仓数、日内亏损、最大回撤、下单频率、黑名单、整手校验，拒绝原因落库。
- **完整指标**：总/年化收益、波动率、夏普、索提诺、最大回撤与持续期、卡玛、胜率、盈亏比、费用、换手、暴露、月度收益、基准超额。
- **Web 终端**：React + TypeScript + Vite + ECharts，覆盖行情、策略、回测、模拟盘、风控与安全设置。
- **安全默认值**：Argon2id 密码、JWT 轮换、TOTP 两步验证、AES-256-GCM 字段加密、SHA-256 审计哈希链、登录锁定与限流。
- **可部署**：多阶段 Docker 构建、Nginx 同源反代、Caddy 自动 HTTPS、SQLite/PostgreSQL、
  健康检查与非 root 运行；依赖锁定（pip/npm lock）+ alembic 迁移，CI 构建镜像、
  服务器一条命令更新（见 docs/DEPLOY.md §11）。

## 架构

```
┌──────────────────────────────────────────────────────────────┐
│  前端 SPA（React / ECharts）                                  │
│  行情 · 策略 · 回测 · 模拟盘 · 实盘 · 基金 · 风控 · 设置       │
└───────────────┬──────────────────────────┬───────────────────┘
                │ REST /api                 │ WebSocket /ws
┌───────────────▼──────────────────────────▼───────────────────┐
│  FastAPI（认证、限流、审计、任务管理）                          │
│                                                              │
│  TradingSession ◄──────────────► SimBroker                   │
│    │  统一会话循环                │  订单队列 / 撮合 / 成交     │
│    │                              │                          │
│    ├─ BacktestEngine（模拟时钟，t+1 撮合）                     │
│    ├─ PaperEngine replay（倍速回放）                           │
│    ├─ PaperEngine live（真实时钟 + 轮询）                      │
│    └─ LiveEngine（真实时钟）──► LiveBroker ──► BrokerAdapter   │
│                                    （ccxt / miniqmt）        │
│                                                              │
│  Strategy ─ RiskManager ─ CostModel ─ Portfolio ─ Metrics     │
│  FundTrade 流水聚合（场外基金，不走引擎）                       │
│                                                              │
│  DataStore（parquet 缓存）→ akshare / csv / synthetic /       │
│                              crypto(ccxt) / fund(净值)        │
│  SQLAlchemy → SQLite / PostgreSQL                             │
│  HKDF → AES-GCM 字段加密 · JWT · TOTP · 审计链                 │
└──────────────────────────────────────────────────────────────┘
```

更详细的分层、事件顺序与扩展点见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)；
框架目录地图与结构审查清单见 [`docs/PROJECT_MAP.md`](docs/PROJECT_MAP.md)；
可渲染的框架图（Mermaid）见 [`docs/FRAMEWORK.md`](docs/FRAMEWORK.md)；
实盘接入步骤与安全边界见 [`docs/LIVE_TRADING.md`](docs/LIVE_TRADING.md)。

## 快速开始（Docker，推荐）

前置：Docker 24+ 与 Compose v2.24+。

```bash
git clone <repo> quant-trading && cd quant-trading

# 生成 .env：随机主密钥、管理员口令、域名
./scripts/init-secrets.sh localhost

# 构建并启动（首次会拉取镜像与依赖）
docker compose up -d --build

# 打开 http://localhost:8080
# 用户名 admin，密码见 .env 的 QUANT_ADMIN_PASSWORD
```

数据源默认为 `akshare`。容器内置了 `data/cache` 的 CSV 历史行情作为离线兜底；
首次启动若外网不可达，可在 `.env` 中改为：

```dotenv
QUANT_DATA_PROVIDER=csv
QUANT_FALLBACK_PROVIDERS=["synthetic"]
```

### 虚拟货币 / 实盘

```dotenv
# 虚拟货币行情：默认 okx；国外交易所直连不畅时配代理
QUANT_CRYPTO_EXCHANGE=okx
#QUANT_CRYPTO_PROXY=http://127.0.0.1:10808

# 实盘总开关（默认 false）：开启后在「实盘」页创建/选择 broker 凭据，
# 默认走测试网；真实资金需再勾选确认。详见 docs/LIVE_TRADING.md
#QUANT_ENABLE_LIVE_TRADING=true
```

用 Docker 跑虚拟货币回测时，把 `QUANT_DATA_PROVIDER` 设为 `crypto`
（或把它加进 `QUANT_FALLBACK_PROVIDERS`），标的写 `CRYPTO:BTC/USDT`。
场外基金的数据源是 `fund`（akshare），通过「基金」页直接使用，不影响主数据链。

登录后建议立即：修改密码 → 启用两步验证 → 保存恢复码。

## 生产部署（HTTPS）

```bash
./scripts/init-secrets.sh quant.example.com
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

- Caddy 自动申请/续期证书（80/443 必须可达，域名已解析）。
- 内部 Nginx 提供静态资源，并把 `/api`、`/ws` 反代到后端，无跨域。
- 默认只暴露 80/443；基础 compose 的 8080 端口在 prod 覆盖层中被清除。
- 若 `.env` 设置了 `COMPOSE_FILE`（叠加 PostgreSQL），显式 `-f` 会覆盖它，
  三个文件都要写：
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.postgres.yml up -d --build`。

### PostgreSQL（推荐）

```bash
# .env 中设置强口令 POSTGRES_PASSWORD
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

已有 SQLite 数据不会自动迁移；需要迁移时先在旧库导出运行记录，或使用 Alembic 自行迁移。

### 运维

```bash
docker compose logs -f backend     # 日志
./scripts/verify.sh                # 容器/API/审计自检
./scripts/backup.sh                # 备份 /app/data（SQLite、行情缓存）
```

**务必备份 `QUANT_MASTER_KEY`**：丢失后所有加密凭据与 TOTP 种子不可恢复。

## 本地开发

```bash
# 后端 + 前端（本机需 Python 3.11+；前端需 Node 20+）
./scripts/dev.sh
```

脚本会创建 `.venv`、安装依赖并启动：

- 后端 <http://127.0.0.1:8000>，交互式 API 文档 `/docs`
- 前端 <http://localhost:5173>，Vite 代理 `/api` 与 `/ws`

也可以只跑后端：

```bash
cd backend
QUANT_ENV=development QUANT_DATA_PROVIDER=csv ../.venv/bin/python -m quant.cli serve --reload
```

首次启动自动生成开发主密钥 `data/master.key`，并创建管理员（未配置 `QUANT_ADMIN_PASSWORD` 时随机生成，见日志）。

## 常用 CLI

```bash
quant serve --host 0.0.0.0 --port 8000     # 启动 Web 服务
quant backtest --strategy dual_ma --symbols 600519,000001 \
               --start 2024-01-02 --end 2024-06-28 --cash 1000000 \
               --output report.json         # 命令行回测（默认 data provider=csv 时，
                                            # 区间需落在 data/cache 种子内，见目录结构说明）
quant fetch --symbols 600519,000001 --years 5   # 预拉行情
quant doctor                                # 环境、密钥、数据库自检
quant keygen --file master.key              # 生成主密钥
quant user create --username alice --password '...' --role user
```

容器内执行：`docker compose exec backend quant doctor`。

## 目录结构

```
quant-trading/
├── backend/
│   ├── quant/
│   │   ├── core/         # 类型、事件、时钟、账户与持仓
│   │   ├── data/         # 数据源、交易日历、parquet 缓存
│   │   ├── execution/    # 费用、交易规则、撮合、模拟券商
│   │   ├── strategy/     # 策略基类/注册表/指标/内置策略
│   │   ├── risk/         # 8 条风控规则与 RiskManager
│   │   ├── engine/       # 统一会话、回测、纸盘
│   │   ├── metrics/      # 绩效指标
│   │   ├── security/     # 加密、密码、JWT、TOTP、审计链
│   │   ├── storage/      # SQLAlchemy 模型/仓储
│   │   ├── server/       # FastAPI、路由、WS、任务管理
│   │   └── cli.py
│   └── tests/
├── frontend/             # React + TypeScript + Vite + ECharts
├── deploy/               # Dockerfile、Nginx、Caddy
├── docs/                 # 架构、安全、部署
├── scripts/              # 密钥初始化、开发、备份、自检
├── legacy/               # 重写前的 backtrader 学习版（归档，不参与构建）
├── data/cache/           # 旧版 CSV 行情种子
├── docker-compose*.yml
└── .env.example
```

## 配置

所有后端配置使用 `QUANT_` 前缀，既可用环境变量，也可用 `.env`。常用项：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `QUANT_ENV` | `development` | `production` 下拒绝自动生成主密钥 |
| `QUANT_MASTER_KEY` | — | 32 字节 base64/hex 根密钥 |
| `QUANT_MASTER_KEY_FILE` | — | 从文件读取根密钥 |
| `QUANT_DATABASE_URL` | `sqlite:///data/quant.db` | 支持 `postgresql+psycopg://...` |
| `QUANT_DATA_PROVIDER` | `akshare` | `akshare` / `csv` / `synthetic` |
| `QUANT_FALLBACK_PROVIDERS` | `["csv"]` | 主源失败后的降级链 |
| `QUANT_DEFAULT_SYMBOLS` | `600519,000001,300059` | 默认股票池（裸代码按 A 股解析） |
| `QUANT_ACCESS_TOKEN_TTL` | `900` | access token 秒数 |
| `QUANT_REFRESH_TOKEN_TTL` | `604800` | refresh token 秒数（轮换） |
| `QUANT_REQUIRE_TOTP` | `false` | 是否强制所有账号启用 2FA |
| `QUANT_MAX_CONCURRENT_BACKTESTS` | `2` | 并发回测上限 |
| `QUANT_ALLOWED_ORIGINS` | localhost | CORS 白名单（JSON 数组） |

完整模板见 [`.env.example`](.env.example)。

## 安全

- 密码：Argon2id（自动升级旧 scrypt 哈希），登录失败锁定。
- 会话：HS256 JWT；access 15 分钟、refresh 7 天且每次刷新轮换、可吊销；access token 只存前端内存。
- 两步验证：TOTP + 一次性恢复码；种子用字段密钥加密。
- 字段加密：HKDF-SHA256 派生子密钥，AES-256-GCM，AAD 绑定字段上下文，密文带版本。
- 审计：SHA-256 哈希链，提供完整性校验接口。
- 传输：生产由 Caddy 终结 TLS；Nginx 注入 CSP、X-Frame-Options 等响应头。
- 容器：非 root、无源码热挂载、主密钥不写入镜像。

细节与威胁模型见 [`docs/SECURITY.md`](docs/SECURITY.md)。

## API

启动后访问 `/docs`（Swagger）或 `/redoc`。主要资源：

```
POST /api/auth/login             登录（可带 TOTP）
POST /api/backtests              提交回测（异步，202；symbols 用 MARKET:CODE，混市场 422）
GET  /api/backtests/{id}         结果：指标、权益曲线、成交、委托
GET  /api/market/markets         市场元信息（CN/US/HK：名称、币种、货币符号）
GET  /api/market/bars            日线（symbol=MARKET:CODE）
GET  /api/market/quote           实时快照（多源聚合，含名称/涨跌/量额/market/currency）
GET  /api/market/search          标的检索（代码/名称，可带 market=CN|US|HK）
POST /api/paper/start            启动模拟盘
GET  /api/paper/state            实时状态
GET  /api/risk/rules             风控规则目录
GET  /api/strategies/custom      我的拼装策略（免代码策略 CRUD，仅本人可见）
POST /api/strategies/custom      创建拼装策略（spec 白名单校验）
GET  /api/admin/users            用户管理（仅管理员）
GET  /api/system/info            系统与加密信息
WS   /ws?token=<access_token>    进度、成交、状态事件
```

## 测试

```bash
cd backend
../.venv/bin/python -m pytest -q
```

覆盖：加密 AAD 防篡改、密码哈希、JWT 类型隔离、撮合与费用（含港美预设）、回测确定性、
风控规则、多市场符号解析与 API 契约、实时快照三市场解析、API 认证、
条件拼装策略（spec 白名单 + 用户隔离 + 引擎端到端；v1 条件与 v2 蓝图节点图）。
后端 pytest 全套 **143 项**（`pytest --collect-only` 与 README/文档口径一致）。

## 许可

MIT。历史学习版代码保留在 `legacy/`，仅作参考。
