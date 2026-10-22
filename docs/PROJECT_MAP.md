# 项目结构与框架审查（PROJECT_MAP）

> 面向维护者的框架地图：一页看懂整个仓库怎么拼起来、数据怎么流、边界在哪、
> 哪些债已还、哪些建议后续还。代码级细节见 [ARCHITECTURE.md](ARCHITECTURE.md)，
> 安全省线见 [SECURITY.md](SECURITY.md)，部署见 [DEPLOY.md](DEPLOY.md)，
> 实盘见 [LIVE_TRADING.md](LIVE_TRADING.md)，框架图见 [FRAMEWORK.md](FRAMEWORK.md)。
> 最近一次全面审查：2026-10-18（236 项测试全绿；§6 为上一轮 143 项时的整改记录，
> §7 已标注本轮完成项）。

## 1. 一页总览

```
┌─────────────────────────────── Web 终端 (React SPA) ───────────────────────────────┐
│ pages: Dashboard Market Strategies(+Blueprint) Backtest Paper Live Funds Risk      │
│        Settings Login                                                             │
│ api/client.ts（fetch + 401 静默刷新）  store: auth/toast/events  hooks/useWebSocket │
└──────────────────────────────────┬────────────────────────────────────────────────┘
                        HTTPS 80/443(prod) ┆ 8080(单机)
┌──────────────────────────────────▼────────────────────────────────────────────────┐
│ nginx(frontend 容器) → /api/* /ws /healthz → backend (FastAPI, quant.server.app)   │
│                                                                                   │
│ server/  app.py 中间件+异常映射 │ routers×11 │ schemas │ deps(鉴权) │ custom_params│
│          backtest_manager(队列 worker) │ ws.EventHub(按用户定向) │ context(装配)        │
│ engine/  backtest(逐日) paper(replay/live) live(实时+真实下单) session(统一时序)   │
│          live_frames(今日活bar) base(结果/净值)                                   │
│ strategy/ base(ParamSpec+ctx 防未来) registry(@注册) custom(v1) custom_graph(v2)   │
│           indicators builtin/{dual_ma,rsi_reversion,momentum,buy_hold}            │
│ execution/ broker(冻结/次日撮合) live_broker(交易所回报记账) matching cost rules   │
│            adapters/{ccxt, miniqmt}（实盘通道，累计口径）                          │
│ funds/   compute_positions(申赎流水→持仓/收益，纯函数)                              │
│ risk/    base.py 8 条规则链（pre_trade 拒单）                                      │
│ metrics/ performance.py（夏普/回撤/月度…）                                        │
│ data/    base(Provider 注册+schema 归一) store(Parquet 缓存+门面) realtime(3源)    │
│          calendar search providers/{akshare, csv, synthetic, crypto, fund}        │
│ storage/ models(含 CompressedJSON/FundTrade) db repositories(9 仓储+RunRecorder)   │
│ security/ crypto(HKDF+AES-GCM) tokens(JWT 轮换) password(Argon2id) totp audit(链)  │
│ core/    types(Bar/Order/Fill/Account, 数量 float) events portfolio clock markets  │
│                                                                                   │
│ DB: SQLite(默认) / PostgreSQL(叠加层)     ws 推送: EventBus→bridge→EventHub→/ws    │
└───────────────────────────────────────────────────────────────────────────────────┘
```

进程模型：**数据库持久化队列 + 进程内 worker**——回测提交写入 `backtest_jobs`，
worker 原子认领（并发上限 `QUANT_MAX_CONCURRENT_BACKTESTS`，队列上限 `QUANT_MAX_QUEUED_BACKTESTS`）；
纸盘 = `PaperManager` 每会话一条后台线程（同用户可多开）；实盘 = `LiveManager`
每（用户, 凭证）一条后台线程。`SessionCoordinator` 用数据库租约/命令协调多实例：
同一会话只由一个实例持有，任意实例可下发 stop/pause/resume/switch；状态快照写入
`session_leases` 供 `/sessions` 跨实例读取。lifespan 统一 shutdown 并释放租约。

## 2. 目录地图（180 个 tracked 文件）

| 路径 | 一句话 |
| --- | --- |
| `backend/pyproject.toml` | 依赖 + ruff/mypy/pytest 配置 + `quant`/`quant-server` 入口（**含未使用的 alembic/orjson，见 §7**） |
| `backend/quant/` | 100+ 个 .py，约 18k 行，上表全部内核 |
| `backend/tests/` | 19 文件（18 个 test_*.py + conftest）/ pytest 收集 **256** 项 |
| `backend/alembic/` | alembic 配置 + 初始迁移（生产启动自动 upgrade，旧库自动 stamp） |
| `.github/workflows/ci.yml` | CI：pytest + 构建前后端镜像推送 GHCR（main 分支） |
| `backend/quant/execution/adapters/` | 实盘适配器：`base`(协议) `ccxt`(虚拟货币) `miniqmt`(A股骨架) |
| `backend/quant/funds.py` | 场外基金流水聚合（纯函数，不进引擎） |
| `backend/quant/news/` | 新闻/政策原始条目 → PIT 因子计算与策略提供器 |
| `backend/quant/strategy/exits.py` | 统一退出原因/决策/优先级 |
| `backend/quant/services/` | 应用服务层 + 会话租约/命令协调 |
| `backend/quant/storage/` | 仓储按聚合拆包 + RunRecorder + 回测任务队列 |
| `backend/quant/engine/live.py` | 实盘引擎 + 按用户隔离的 LiveManager |
| `backend/quant/data/aggregate.py` | 日线 → 周/月 K 线重采样（周对齐周五、月对齐月末最后交易日） |
| `backend/quant/data/indices.py` | 内置指数清单 + 腾讯批量指数快照（独立 id，不进 MARKET:CODE） |
| `backend/quant/data/providers/crypto_provider.py` | ccxt 交易所 K 线/快照/白名单检索/全周日历 |
| `backend/quant/data/providers/fund_provider.py` | akshare 场外基金净值（映射为 OHLCV 供图表复用） |
| `frontend/src/` | 34 文件 ~9k 行：`pages/`(12) `components/`(12+blueprint/6) `api/` `store/` `hooks/` `styles/` |
| `frontend/global.css` | 单一样式表 1.5k 行（暗色令牌:root，A 股红涨绿跌） |
| `deploy/backend.Dockerfile` | daocloud python:3.12-slim， COPY 种子 CSV，非 root uid 10001，`CMD quant serve --workers 1` |
| `deploy/frontend.Dockerfile` | 1ms.run node:20-alpine（daocloud 502 备案）→ nginx:1.27-alpine |
| `docker-compose.yml` + `postgres/dev/prod` | 1 base + 3 覆盖层（`COMPOSE_FILE` 叠加）；prod 用 `!reset` 清端口并由 Caddy 接管 80/443 |
| `scripts/` | `init-secrets.sh` `dev.sh` `verify.sh` `backup.sh` `update.sh`(持续更新) `migrate.sh`(迁移) `lock-deps.sh`(依赖锁) |
| `docs/` | 本文件 + ARCHITECTURE + DEPLOY + SECURITY + **LIVE_TRADING** + FRAMEWORK |
| `data/cache/*.csv` | 2020-01-02~2024-06-28 的 600519/000001 种子，随 backend 镜像出货（csv provider 离线用） |
| `legacy/` | backtrader 学习版归档 527 行，不参与构建/部署/测试 |

## 3. 关键链路

### 3.1 一次回测提交

```
POST /api/backtests → routers/backtest → schemas 校验 → inject_custom_spec(custom_strategy_id
  → storage 按用户取 spec, 注入 params["spec"]) → BacktestManager(202 run_id)
→ 线程: BacktestEngine.run → TradingSession.step({T+1 解冻→broker 撮合昨单→重估→
  strategy.on_bars→strategy.submit→risk 规则链判定拒绝或入队}) → EventBus
→ ① RunRecorder 落 runs/streams(权益每 500 点落库, finalize 压缩 JSON)
  ② backtest_manager.forward → ws.EventHub → /ws（按 user_id 定向, 断线回放 200 条）
→ metrics/performance 成 PerformanceReport → 200 结果 + 事件 run.finished
```

### 3.2 策略双通道与订单纪律分界

| | v1 表单拼装 | v2 蓝图编辑器 |
| --- | --- | --- |
| spec | `{entry[],exit[],…}` ≤6 条/侧、≤8KB | `{nodes≤48, edges≤96, outputs, …}` ≤16KB |
| 解析 | `custom.parse_spec` | `custom.parse_spec` 分派 → `custom_graph.parse_graph_spec` |
| 类型线 | 操作数三元（字段/指标/常数） | 端口类型连线（青=数值/橙=布尔/灰=常数），布尔才能进 outputs |
| lookback | `lookback + 2` | `lookback + 1`（cross 自带前一根） |
| 回测信号 | 金叉/ANY 组合逐日 | 可达剪枝后拓扑求值 |
| UI | `StrategyBuilder.tsx` | `blueprint/`（domain↔白名单、compile 双向、三模板） |

**两者共用**：`CustomRuleStrategy` 的订单纪律——次日开盘撮合、整数手、止损/止盈优先于
信号离场、仓位参数、风控链前置。蓝图只表达信号，不表达订单语义。

### 3.3 数据链

Provider（akshare 免费直连 / csv 种子 / synthetic 确定性 / crypto ccxt / fund akshare 净值）→
`normalize_frame` 统一 schema → `DataStore` Parquet 缓存（命中窗口判 3 天缓冲）→
`MarketDataService.bars()` 多源降级 → 引擎逐 `交易日并集` 推进；实时快照 `realtime.py`：
腾讯→东财→新浪，TTL + stale 标记。

- 虚拟货币：`CRYPTO:BTC/USDT`，ccxt `fetch_ohlcv` 分页，7×24 全周日历，缓存名把 `/` 转义为 `~`。
- 场外基金：`FUND:007005`，净值映射为 OHLCV（volume=0）仅供图表；**不走引擎**。

### 3.4 实盘链路（2026-10 新增）

```
POST /api/live/start（三重闸门：总开关 → 测试网/确认 → 凭据归属+审计）
→ LiveEngine(后台线程) → 每轮：
   ① broker.sync()：Adapter 拉累计成交/状态 → LiveBroker 算增量 → Account 记账 → on_fill
   ② 取价（adapter.quote → data.realtime 兜底）
   ③ TradingSession.step：策略决策 → ctx.submit → 风控 → LiveBroker.submit → Adapter 下单
   ④ 跨日：撤昨日 DAY 单（GTC 保留）
   ⑤ 每 5 分钟 broker.reconcile()：本地镜像 vs 交易所，差异写日志/WS
→ POST /api/live/stop：session.finish → cancel_all 逐单撤单
```

细节见 [LIVE_TRADING.md](LIVE_TRADING.md)。

### 3.5 场外基金链路

```
POST /api/funds/trades（申购/赎回）→ fund_trades 流水表
GET /api/funds/holdings → compute_positions(流水) + 最新净值 → 份额/成本/收益
GET /api/funds/nav → FundProvider(akshare) 历史净值 → NavChart
```

### 3.6 前端契约维护点（手写 ≠ 生成）

- `api/types.ts` 全部手写镜像，`RunDetail` 明细为 `dict[str, Any]` 型表面（见 §7-A 第 6 条）
- `change_pct` 是**百分数**（唯一单位分叉点，仅 Market 页记得换算）
- WS 事件名 `run.*` 与 `custom_rule` 字符串两端散布（后续常量化，见 §7-B）
- `priceDigits(market, price)`：港股 3 位 / 虚拟货币按量级 / 基金 4 位 / 其余 2 位

## 4. 数字盘点

| 维度 | 数值 |
| --- | --- |
| Python | 19.4k 行（quant 16.7k / tests 2.7k / legacy 527） |
| TypeScript(TS+TSX) | 9.1k 行（34 文件） |
| 测试 | 17 文件，收集 **236** 个（含参数化展开） |
| 前端测试 | ❌ 无（无 vitest/jest/playwright） |
| CI | ❌ 无（无 .github/、无 pre-commit） |
| lockfile | ✅ 前端 `package-lock.json` + 后端 `requirements.lock.txt` |
| 环境变量 | `.env.example` 27 个 key，与服务端消费/组件脚本全对齐 |

## 5. 约定（新增代码请遵守）

- **策略**：`strategy/builtin/` 加文件即可，`@register_strategy` + ParamSpec 自描述 →
  前端表单零改动；订单纪律（T+1/撮合/风控）归引擎，策略只产信号与目标仓位。
- **指标**：纯函数写在 `strategy/indicators.py`；v1/v2 共用 `INDICATOR_SPECS` 一张白名单。
- **风控**：`RULE_TYPES` 加类 + `routers/risk.py` 预设目录同步。
- **验证边界**：所有用户输入到执行层的 spec/参数必须过服务端白名单（不得依赖前端校验）。
- **数据**：任何 symbol 一律 `MARKET:CODE`，解析在 `core/markets.py`；
  新市场 = `Market` 枚举 + `BoardRules` + `CostModel.for_market` + 日历 + provider + 前端 `format.ts` 四表。
- **实盘**：新通道实现 `execution/adapters/base.BrokerAdapter` 并在 `adapters/__init__.py` 注册；
  **不要**在适配器里做记账或风控（那是 `LiveBroker`/`RiskManager` 的职责）；
  回报必须是累计口径（成交量/均价/费用）。
- **基金**：只记流水，不建持仓表；聚合逻辑放 `quant/funds.py` 纯函数。
- **订单数量**：全链路 `float`；整手约束只在 `is_valid_lot` / 撮合层（`lot_size>1` 时生效）。
- **测试**：默认放 `backend/tests/`，工具夹具只在 `conftest.py`；网络全部 mock。

## 6. 2026-10-18 审查整改（已完成）

| 级别 | 位置 | 问题 → 处理 |
| --- | --- | --- |
| Bug | `strategy/custom.py` | v2 字符串 spec 超 16KB 时 `NameError`（`MAX_GRAPH_SPEC_BYTES` 缺位）→ 补 import + 回归测试 |
| Bug | `risk/base.py` | `from_config` 把显式 `[]` 吞成默认规则 → 改 `is not None`，空列表真正零风控（新测试锁定） |
| Bug | `cli.py` | `--no-risk` 两分支等价永远传 None → 改传 `[]`（配合上一条生效） |
| Bug | frontend `FlowEditor.applyTemplate` | graphToCanvas 已含哨兵，外层又叠 markerNodes → 模板路径重复节点 id，改为空白模板单独补哨兵 |
| 死代码 | 7 处 | `slippage_cost / quote_row_to_dict / fee_from_dict / filter_trading_days / annualized_volatility / make_snapshot_dict / on_tick(+_try_match 零量分支 + _bar_with_volume)` 全部删除（含 __all__/import 清理），零引用 grep 确认 + 143 测试 |
| 副作用 | `server/app.py` | 模块级 `app = create_app()` import 即建库建密钥 → 删除（`uvicorn quant.server.app:app` 均改用 factory 路径） |
| UI | `Layout.tsx` | 蓝图页缺 PAGES 条目顶栏标题退化 → 补 |
| UI | `StrategyBuilder.tsx` | v1 字段下拉后端有 6 字段前端只露 4 → 补 `volume/amount` |
| UI | `global.css` | `.toast-info` 类无样式 → 补（青强调） |
| 配置 | `.env.example` | `QUANT_MASTER_KEY_FILE` / `QUANT_MAX_CONCURRENT_BACKTESTS` / `QUANT_SECURE_COOKIES` 缺失 → 补注释行 |
| git | `.gitignore` | SQLite WAL 残件 `*.db-wal/-shm` → 补 |
| 文档 | README | 141→143 实测口径、CLI 例子超出 CSV 种子区间 → 校准；ARCHITECTURE §13 漏列 test_paper_manager → 补 |

## 7. 继续还账建议（未做，按优先级）

> 2026-10-18 功能扩展（虚拟货币 / 基金 / 实盘）已完成 §7-A 第 1、2 项的代码迁移：
> 指标白名单已收敛到 `indicators.INDICATOR_SPECS`；板块/涨跌停规则已上移 `core.markets`。
> 注意 **`data/` 与 `risk/` 仍有 5 处 import 指向 `execution.rules` 的再导出**（功能等价，
> 但越层依赖的"形式"还在）——收尾只需把这 5 处改指 `core.markets` 再删 shim。

### A. 结构债（下一次大动作值得做）

1. ~~**v1/v2 指标白名单重复**~~ ✅ 已收敛到 `indicators.py` 一张 spec 表（含参数边界）。
2. ~~**data→execution 反向依赖**~~ ✅ 规则本体已上移 `core.markets`；剩余 5 处 import 待改指。
3. **config 序列化三份**：`engine base._config_dict / backtest_manager._config_payload /
   routers.paper 手写` → 收敛成 EngineConfig 一份 to_dict（新增的 `LiveEngine._config_dict`
   让这处债又多了一份）。
4. **WS 转发两套**：`ws.bus_bridge` 与 `BacktestManager.forward` 逻辑重复，留一个。
5. **repositories 内嵌加解密**：storage 混入 `security` 职责，可拆出 CredentialCodec。
6. **RunDetail 强类型**：明细仍 `dict[str, Any]`，用 openapi-typescript 或 Pydantic→TS
   生成可一次消灭手写镜像的 drift（引擎改字段名 = 前端类型变谎言，是慢失故障源）。
7. ~~**alembic 决断**~~ ✅ 已配置 `backend/alembic/` + 初始迁移；生产启动自动
   upgrade（旧库自动 stamp），`scripts/migrate.sh` 可手动执行。
8. **utcnow 贯彻**：项目自建 `core.types.utcnow()`（aware），但 engine/server/storage 域内
   仍有 ~8 处 naive `datetime.now()/utcnow()`（Py3.12 起后者已弃用）。
9. **实盘多副本互斥**：`LiveManager` 是进程内状态，多副本部署时同一账号可能在两个实例各开
   一个会话；生产需单实例或外部锁（Redis/DB 租约）。
10. **实盘订单状态机**：`OrderStatus` 仍只有本地七态，交易所侧"已报待撤/部成待撤"等
    中间态被折叠成 open；需要更细的前端展示时再扩枚举。

### B. 工程件

- ~~`frontend/package-lock.json`~~ ✅ 已生成，镜像改用 `npm ci`；后端新增
  `deploy/requirements.lock.txt`（`scripts/lock-deps.sh` 维护）
- ESLint + Prettier（frontend 目前 `lint = tsc`，且 2 处 `eslint-disable` 注释无效）
- pre-commit / GitHub Actions：ruff + mypy + pytest + compose build smoke（步骤见 DEPLOY §9）
- ~~**backend.Dockerfile 依赖层缓存失效**~~ ✅ 已改两段式：先 `pip install -r requirements.lock.txt`
  → COPY 源码 → `pip install --no-deps`，改码重建只重装本地包（秒级）。
- 常量化散落字符串：`custom_rule`（3 页）、`run.*` 事件名、localStorage key

### C. 测试覆盖地图（当前空白区）

| 区域 | 现状 |
| --- | --- |
| CLI 6 个命令 | 0（`--no-risk` 死开关得以存活的原因） |
| auth 登录锁定/refresh 轮换/TOTP/审计端点 | 仅 conftest 登录夹具，无端点级 |
| admin / credentials / system 路由 | 0 |
| akshare provider 拉取 & CSV provider & 日历 | 仅 realtime 解析有覆盖 |
| 回测 429/403 等路由分支 | 仅偕同 spec 测试命中 happy path |
| paper live 模式（live 轮询分支） | 0 |
