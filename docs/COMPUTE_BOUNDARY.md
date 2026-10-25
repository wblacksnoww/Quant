# 计算下沉边界（C++ / Python 分工）

> 这份文档回答一个问题：**改了策略逻辑之后，我需要碰 C++ 吗？**
>
> 结论：绝大多数情况下**不需要**。策略用 spec 表达，spec 是数据不是代码。
> 只有新增「指标」或新增「撮合规则」才需要动 C++。

---

## 1. 一句话总览

```
Python：拿到什么数据、用哪个策略、结果怎么展示
C++   ：数据点之间怎么算（指标 / 信号 / 撮合 / 记账）
```

Python 是**业务层**（路由、鉴权、数据抓取、参数校验、结果序列化）；
C++ 是**计算层**（一切"逐 bar / 逐 tick 的循环"）。

判断标准很简单：**如果一段代码在循环体内、且循环次数随数据量线性增长，它就该在 C++。**

---

## 2. 边界表

| 环节 | 位置 | 文件 | 为什么放这儿 |
|---|---|---|---|
| HTTP 路由 / 鉴权 | Python | `api/routes/*.py` | 网络 IO，无计算 |
| 数据抓取（AkShare/CCXT） | Python | `data/providers/*.py` | 网络 IO，第三方 SDK |
| 数据缓存 / 复权 / 交易日历 | Python | `data/store.py` `data/base.py` | 一次性 O(n)，非热路径 |
| 参数校验 | Python | `schemas.py` | 边界层，失败要给人话 |
| spec 语法校验 | Python | `strategy/custom.py` | 校验只跑一次，报错要可读 |
| **指标计算（SMA/EMA/RSI/ATR/BOLL…）** | **C++** | `cpp/indicators.cpp` | 逐 bar 循环 |
| **spec 编译** | **C++** | `cpp/strategy.cpp` `CompiledSpec` | 树形求值，逐 bar 跑 |
| **信号求值** | **C++** | `cpp/strategy.cpp` `SpecState::step` | 逐 bar 循环 |
| **撮合（限价/涨跌停/成交量上限）** | **C++** | `cpp/core.cpp` | 逐单循环 |
| **成本 / 滑点 / 冲击** | **C++** | `cpp/core.cpp` | 逐笔计算 |
| **账户记账 / 风控** | **C++** | `cpp/core.cpp` | 逐笔计算 |
| **bar 级回测主循环** | **C++** | `cpp/engine.cpp` | 逐 bar 循环 |
| **tick 级回测主循环** | **C++** | `cpp/tick.cpp` | 逐 tick 循环 |
| 纸盘 / 实盘 step 调度 | Python | `engine/paper.py` `engine/live.py` | 有网络、线程、重连 |
| 结果封装 / 绩效指标 | Python | `metrics/performance.py` | 对权益曲线算一次 |
| 落库 / 序列化 | Python | `engine/*.py` | IO |

---

## 3. 三张图

### 3.1 bar 级回测的数据流

```
Python                          │            C++
────────────────────────────────┼──────────────────────────────
EngineConfig + params           │
      │                         │
      ├─ spec_for_strategy() ───┼──► CompiledSpec（编译一次）
      │                         │
      ├─ 取日线 → numpy 数组 ───┼──► run_backtest(ohlcv, spec, cfg)
      │                         │        │
      │                         │        ├─ 指标预计算
      │                         │        ├─ 逐 bar: SpecState::step
      │                         │        ├─ 撮合 / 成本 / 记账
      │                         │        └─ 权益快照
      │                         │        ▼
      ├─ 结果包装 ◄─────────────┼─── fills + equity_curve
      ├─ compute() 绩效          │
      └─ 落库                    │
```

**关键**：Python 与 C++ 之间只有 **2 次调用**（进、出），不是逐 bar 往返。

### 3.2 tick 级回测的数据流

```
Python                          │            C++
────────────────────────────────┼──────────────────────────────
TickProvider.fetch()            │
      │  (分钟线插值 / 未来 L2)  │
      ├─ TickFrame ─────────────┼──► run_tick_backtest(
      │  ts/price/bid/ask/      │       spec, ts, price, bid,
      │  volume/amount          │       ask, volume, amount, cfg)
      │                         │        │
      │                         │        ├─ ① 用上一根 bar 信号撮合
      │                         │        ├─ ② tick 级止损/止盈
      │                         │        ├─ ③ bar 收线 → 窗口重喂求值
      │                         │        └─ ④ 权益快照
      │                         │        ▼
      └─ 结果包装 ◄─────────────┼─── fills + equity_curve
```

**为什么整段下沉**：一天 4800 个 tick、十年 1200 万次。
每次 pybind 往返约 1~2 微秒，光跨语言开销就是 12~24 秒 —— 比纯 Python 还慢。
所以 tick 循环**必须**一次调用跑完。

### 3.3 拼装策略是怎么变成 C++ 的

```
用户在界面上拼条件
      │
      ▼
   spec JSON  ◄──── 这是"数据"，不是代码
{ "entry": [{"type":"cross_above",
             "left": {"kind":"indicator","name":"sma","params":{"period":10}},
             "right":{"kind":"indicator","name":"sma","params":{"period":30}}}],
  "exit": [...], "position_pct": 0.95 }
      │
      ▼  C++ 编译（一次）
  CompiledSpec
      │  把 JSON 树拍平成指令数组，指标句柄预先绑定
      ▼  逐 bar 求值
  SpecState::step(bar, columns) → Signal
```

**所以"拼装策略最终形成的也是 C++"这个目标已经达成** ——
用户拼出来的东西最终是 `CompiledSpec`，在 C++ 里跑，Python 一行求值代码都没有。

### 3.4 研究通道：用 Python 写算子做快速验证

生产路径是 C++，但**试新想法**时改 C++ 太慢（改代码 → 重编译 → 重启，几分钟）。
所以留了一条明确划界的快捷通道：

```
你想试一个新指标
      │
      ▼  用 Python 写（一行装饰器）
@register_operator("zscore", period=("int", 20))
def _zscore(frame, period=20):
    close = frame["close"]
    return (close - close.rolling(period).mean()) / close.rolling(period).std()
      │
      ▼  在 spec 里当字段用
{"kind": "field", "field": "op:zscore(period=20)"}
      │
      ▼  引擎全序列预计算 → extras 列 → 喂给 C++
C++ 主循环一行都不用改（Columns::at() 本来就从 extras 按列名取值）
```

**关键在于 C++ 完全不需要改动。** 新闻字段走的就是这条路，
算子只是把"Python 算好一列数据"这个机制用到指标上。

用法与边界见 `backend/quant/strategy/operators.py` 的文件头注释。三条纪律：

| 纪律 | 为什么 |
|---|---|
| 算子必须**因果**（第 i 个输出只用 `src[0..i]`） | 无法自动强制，用 `assert_causal()` 抽查拦截最常见几类（`shift(-1)`、全样本标准化） |
| warmup 段返回 **NaN 而非 0** | C++ 里 `NaN > x` 为 false（条件不成立），而 `0 > x` 可能为 true（假信号） |
| 跑出来必须**标注是研究通道** | 进生产要在 `cpp/indicators.cpp` 重写并过等价性测试，两者结果不可混用 |

引擎会在 `result.logs` 里显式记录两类信息：
用了哪些算子（info）、哪些算子不可用及原因（error）。
不可用算子的列全为 NaN，**回测会跑完但零成交** —— 这个提示必须让用户看到，
否则使用者会以为"策略失效"，实际是算子没接上。

---

## 4. 什么时候需要碰 C++

只有两种情况：

### 情况 A：新增指标

1. `cpp/indicators.hpp` 声明 `void my_indicator(const double* src, size_t n, ...)`
2. `cpp/indicators.cpp` 实现（注意：必须**因果**，第 i 个输出只能用 ≤ i 的输入）
3. `cpp/strategy.cpp` 的指标工厂里注册名字
4. 在 Python 侧 spec 校验的白名单里加名字

### 情况 B：新增撮合规则

改 `cpp/core.cpp` 的 `try_match` 或 `cpp/tick.cpp` 的 `run_tick_backtest_one`。

### 不需要碰 C++ 的情况

- 调参数（`fast=10 → 20`）：纯数据
- 换条件组合：纯数据
- 新增策略（用现有的指标和条件类型）：纯数据
- 改风控阈值：`EngineConfig` 字段
- **试一个新指标**：用 `@register_operator` 写 Python 算子，见 §3.4

最后一项是刻意的例外：研究阶段"试一个新指标"的频率远高于其它改动，
为了不让编译拖慢迭代，指标可以先用 Python 写、作为 extras 列进 C++。
**验证有效的算子仍需在 C++ 重写一遍**才算进生产 —— 这是效率与可信度的交换。

---

## 5. 已做的下沉

| 缺口 | 之前 | 现在 |
|---|---|---|
| benchmark | 有 benchmark 就整条降级 Python | C++ 内对齐 + 算 Beta/Alpha/TE/IR |
| news_dual_ma | 整个策略在 Python | `build_spec()` 生成含新闻条件的 spec，走 C++ |
| tick 级 | 不存在 | `cpp/tick.{hpp,cpp}` 整段下沉 |
| 自定义指标 | 必须改 C++ 重编译 | Python 算子 → extras 列（研究通道） |

`native_ready()` 现在不再因 benchmark 返回 False；`NATIVE_STRATEGIES` 6 个策略全部可用。

---

## 6. 保留 Python 的部分及理由

### 研究通道（Python 算子）

见 §3.4。这是**刻意保留**的能力，不是还没下沉干净的残留：
试新想法的迭代速度比运行速度更重要，而研究阶段的数据量小到 Python 完全够用。
把它和"生产路径"分开标注，是为了避免研究结论被误当成生产结论。

### 纸盘 / 实盘 step 调度
信号求值已经走 C++（`SpecState::step`），但**逐 bar 推进的循环**留在 Python。
理由：那个循环里混着网络请求、线程同步、断线重连、订单状态回调 ——
这些塞进 C++ 只会让异常处理更难写，且不是性能瓶颈（一天 240 根 bar，
真正的耗時在等网络，不在算指标）。

### 绩效指标
`metrics/performance.py` 在**一次回测结束后**对几千个点算一遍，O(n) 且只跑一次。
放 Python 的收益（numpy/pandas 生态、易调试）远大于成本。

---

## 7. 两条腿并存期间的纪律

`_run_native` 与 `_run_python` 同时存在（任务 #17 之前不删），
目的是让 `test_native_equivalence.py` 能逐笔对比。

**纪律：任何 C++ 逻辑改动都必须先让等价性测试通过。**

理由：等价性测试的价值来自两条腿独立实现。删掉 Python 腿就失去比对基线 ——
这也是为什么删除顺序是「先验证再删」，不是「先删再修」。

---

## 8. 已知局限（别当结论用）

| 局限 | 影响 | 状态 |
|---|---|---|
| 合成 tick 来自分钟线插值 | 只有 OHLC 路径，无跳跃/扫单/流动性枯竭 | 代码内已标注 warning，只能验机制不能判优劣 |
| A 股无历史 tick | 回落到日线后 tick 粒度粗到一天几笔 | provider 层可替换，等数据源 |
| `momentum_rotation` 需横截面 | tick 通道显式抛 `ValueError` | 语义决定，不该硬塞 |
| tick 撮合是简化版 | 只按 price 判断成交，无队列位置/微观结构 | 需真实 L2 才有意义 |

### 插值路径的可信度（实测）

用 2024 年 120 个交易日、5 元起的合成分钟线做交叉验证：

| 指标 | 真实 bar | 生成 tick | 结论 |
|---|---|---|---|
| 首价 → 末价 | 5.000 → 9.133 | 5.000 → 9.133 | 一致 |
| 极值 max/min | 121.209 / 7.9366 | 121.209 / 7.9366 | 一致 |

**结论**：插值不改变价格路径的端点与极值，tick 与 bar 同源同形。
所以收益失真**不来自插值**，而来自"路径形状" —— 真实 tick 有跳空、
扫单、瞬时流动性枯竭，插值的三段折线把这些平滑掉了。

**另一个必须知道的陷阱**：横盘价格下若插值重复踩 high/low，会凭空造出利润。
`test_flat_price_returns_only_fees` 专门守这条 —— 常数价格下收益必须只反映手续费。
