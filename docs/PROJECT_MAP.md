# 项目结构与框架审查（PROJECT_MAP）

> 面向维护者的框架地图：一页看懂整个仓库怎么拼起来、数据怎么流、边界在哪、
> 哪些债已还、哪些建议后续还。代码级细节见 [ARCHITECTURE.md](ARCHITECTURE.md)，
> 安全省线见 [SECURITY.md](SECURITY.md)，部署见 [DEPLOY.md](DEPLOY.md)。
> 最近一次全面审查：2026-10-18（143 项测试全绿，随后的整改见 §6）。

## 1. 一页总览

```
┌─────────────────────────────── Web 终端 (React SPA) ───────────────────────────────┐
│ pages: Dashboard Market Strategies(+Blueprint) Backtest Paper Risk Settings Login │
│ api/client.ts（fetch + 401 静默刷新）  store: auth/toast/events  hooks/useWebSocket │
└──────────────────────────────────┬────────────────────────────────────────────────┘
                        HTTPS 80/443(prod) ┆ 8080(单机)
┌──────────────────────────────────▼────────────────────────────────────────────────┐
│ nginx(frontend 容器) → /api/* /ws /healthz → backend (FastAPI, quant.server.app)   │
│                                                                                   │
│ server/  app.py 中间件+异常映射 │ routers×9 │ schemas │ deps(鉴权) │ custom_params │
│          backtest_manager(线程池) │ ws.EventHub(按用户定向) │ context(装配)        │
│ engine/  backtest(逐日) paper(replay/live) session(统一时序) base(结果/净值)       │
│ strategy/ base(ParamSpec+ctx 防未来) registry(@注册) custom(v1) custom_graph(v2)   │
│           indicators builtin/{dual_ma,rsi_reversion,momentum,buy_hold}            │
│ execution/ broker(冻结/次日撮合) matching(一字板/量能/停牌) cost(佣金税滑) rules    │
│ risk/    base.py 8 条规则链（pre_trade 拒单）                                      │
│ metrics/ performance.py（夏普/回撤/月度…）                                        │
│ data/    base(Provider 注册+schema 归一) store(Parquet 缓存+门面) realtime(3源)    │
│          calendar search  providers/{akshare, csv, synthetic}                     │
│ storage/ models(含 CompressedJSON 列) db(引擎工厂) repositories(7 仓储+RunRecorder)│
│ security/ crypto(HKDF+AES-GCM) tokens(JWT 轮换) password(Argon2id) totp audit(链)  │
│ core/    types(Bar/Order/Fill/Account) events(EventBus) portfolio clock markets    │
│                                                                                   │
│ DB: SQLite(默认) / PostgreSQL(叠加层)     ws 推送: EventBus→bridge→EventHub→/ws    │
└───────────────────────────────────────────────────────────────────────────────────┘
```

进程模型：**无消息队列、无调度器**——回测 = `BacktestManager` 每任务一线程（并发上限
`QUANT_MAX_CONCURRENT_BACKTESTS`，超限 429）；纸盘 = `PaperManager` 每用户一条后台线程；
lifespan 统一 shutdown。

## 2. 目录地图（155 个 tracked 文件）

| 路径 | 一句话 |
| --- | --- |
| `backend/pyproject.toml` | 依赖 + ruff/mypy/pytest 配置 + `quant`/`quant-server` 入口（**含未使用的 alembic/orjson，见 §7**） |
| `backend/quant/` | 73 个 .py，约 13.5k 行，上表全部内核 |
| `backend/tests/` | 9 文件 / pytest 收集 **143** 项（§6 整改后） |
| `frontend/src/` | 32 文件 ~8k 行：`pages/`(9) `components/`(7+blueprint/6) `api/` `store/` `hooks/` `styles/` |
| `frontend/global.css` | 单一样式表 1.5k 行（暗色令牌:root，A 股红涨绿跌） |
| `deploy/backend.Dockerfile` | daocloud python:3.12-slim， COPY 种子 CSV，非 root uid 10001，`CMD quant serve --workers 1` |
| `deploy/frontend.Dockerfile` | 1ms.run node:20-alpine（daocloud 502 备案）→ nginx:1.27-alpine |
| `docker-compose.yml` + `postgres/dev/prod` | 1 base + 3 覆盖层（`COMPOSE_FILE` 叠加）；prod 用 `!reset` 清端口并由 Caddy 接管 80/443 |
| `scripts/` | `init-secrets.sh`(生成 .env) `dev.sh`(本地热重载) `verify.sh`(部署自检) `backup.sh` |
| `docs/` | 本文件 + ARCHITECTURE(13 节) + DEPLOY(10 节) + SECURITY(12 节) |
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

Provider（akshare 免费直连 / csv 种子 / synthetic 确定性）→ `normalize_frame` 统一 schema →
`DataStore` Parquet 缓存（命中窗口判 3 天缓冲）→ `MarketDataService.bars()` 多源降级 →
引擎逐 `交易日并集` 推进；实时快照 `realtime.py`：腾讯→东财→新浪，TTL + stale 标记。

### 3.4 前端契约维护点（手写 ≠ 生成）

- `api/types.ts` 全部手写镜像，`RunDetail` 明细为 `dict[str, Any]` 型表面（见 §7-A 第 6 条）
- `change_pct` 是**百分数**（唯一单位分叉点，仅 Market 页记得换算）
- WS 事件名 `run.*` 与 `custom_rule` 字符串两端散布（后续常量化，见 §7-B）

## 4. 数字盘点

| 维度 | 数值 |
| --- | --- |
| Python | 16.2k 行（quant 13.5k / tests 2.0k / legacy 527） |
| TypeScript(TS+TSX) | 8.1k 行（32 文件） |
| 测试 | 9 文件，收集 **143** 个（含参数化展开） |
| 前端测试 | ❌ 无（无 vitest/jest/playwright） |
| CI | ❌ 无（无 .github/、无 pre-commit） |
| lockfile | ❌ 前端无 package-lock.json（镜像构建不可复现） |
| 环境变量 | `.env.example` 26 个 key，与服务端消费/组件脚本全对齐 |

## 5. 约定（新增代码请遵守）

- **策略**：`strategy/builtin/` 加文件即可，`@register_strategy` + ParamSpec 自描述 →
  前端表单零改动；订单纪律（T+1/撮合/风控）归引擎，策略只产信号与目标仓位。
- **指标**：纯函数写在 `strategy/indicators.py`；v1/v2 白名单分层登记。
- **风控**：`RULE_TYPES` 加类 + `routers/risk.py` 预设目录同步。
- **验证边界**：所有用户输入到执行层的 spec/参数必须过服务端白名单（不得依赖前端校验）。
- **数据**：任何 symbol 一律 `MARKET:CODE`，解析在 `core/markets.py`。
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

### A. 结构债（下一次大动作值得做）

1. **v1/v2 指标白名单重复**：`custom.py:_INDICATORS` 与 `custom_graph.py:_INDICATORS` 几乎相同，
   建议收敛到 `indicators.py` 一张 spec 表（含参数边界），两处解释器只读它。
2. **data→execution 反向依赖**：`akshare_provider/search` 为了 `price_limits/board_of` import
   执行层 → 这两个函数本质属于 `core.markets`，上移后 data/risk/strategy 分层瞬间全部干净
   （当前为项目唯一越层依赖）。
3. **config 序列化三份**：`engine base._config_dict / backtest_manager._config_payload /
   routers.paper 手写` → 收敛成 EngineConfig 一份 to_dict。
4. **WS 转发两套**：`ws.bus_bridge` 与 `BacktestManager.forward` 逻辑重复，留一个。
5. **repositories 内嵌加解密**：storage 混入 `security` 职责，可拆出 CredentialCodec。
6. **RunDetail 强类型**：明细仍 `dict[str, Any]`，用 openapi-typescript 或 Pydantic→TS
   生成可一次消灭手写镜像的 drift（引擎改字段名 = 前端类型变谎言，是慢失故障源）。
7. **alembic 决断**：pyproject 声明但零配置——要么补 `alembic init`，要么删依赖
   （当前仅 create_all，PostgreSQL 生产 schema 变更无路径）。
8. **utcnow 贯彻**：项目自建 `core.types.utcnow()`（aware），但 engine/server/storage 域内
   仍有 ~8 处 naive `datetime.now()/utcnow()`（Py3.12 起后者已弃用）。

### B. 工程件

- `frontend/package-lock.json`（锁定依赖，镜像构建可复现）
- ESLint + Prettier（frontend 目前 `lint = tsc`，且 2 处 `eslint-disable` 注释无效）
- pre-commit / GitHub Actions：ruff + mypy + pytest + compose build smoke（步骤见 DEPLOY §9）
- **backend.Dockerfile 依赖层缓存失效**：`deploy/backend.Dockerfile:22-27` 先整目录 COPY `backend`
  再 `pip install "/app/backend[postgres]"`——任何 quant 源码变更都会重下全部 wheel
  （慢速镜像网络下 ~20min）。两段式（COPY pyproject → `pip install` 依赖
  → COPY 源码 + `--no-deps` 装包）可把日常改码重建降到秒级。
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
