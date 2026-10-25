# C++ 下沉验证报告

> 用途：删除 Python 回测腿（任务 #17）的**前置证据**。
> 用户定的原则是「先验证再删」—— 删掉 Python 腿会失去比对基线，
> 所以必须先证明两条腿一致，且证明是**可复现**的，而不是某次跑通。
>
> 复现方式见文末。最后更新：2026-10-25。

---

## 1. 环境

| 项 | 值 | 说明 |
|---|---|---|
| 解释器 | CPython 3.12.15 | `/home/blacksnow/.local/share/uv/python/cpython-3.12.15-linux-x86_64-gnu/` |
| ABI | `cpython-312` | 必须与 `.so` 标签一致，否则 ImportError 后**静默回退** |
| venv | `/tmp/qenv312` | pybind11 3.1.0 / numpy 2.5.4 / pandas 3.1.0 |
| 扩展 | `backend/quant/_core.cpython-312-x86_64-linux-gnu.so` | 790424 字节 |

> ⚠️ **ABI 不匹配是静默失败**，不会报错，只会悄悄走 Python 路径，
> 于是"C++ 测过了"变成幻觉。上一轮记录的 "294 passed / 8 failed"
> 就是这个原因 —— venv 是 3.13，`.so` 是 `cpython-312`。
> 换环境后同一份代码是 331 passed / 0 failed。

---

## 2. 自动化测试结果

| 套件 | 结果 | 守什么 |
|---|---|---|
| `test_native_equivalence.py` | **8 / 8** | 两条腿逐笔、逐点对齐 |
| `test_invariants.py` | **22 / 22** | 现金≥0、成交≤成交量、T+1、挂单超龄 |
| `test_tick_engine.py` | **21 / 21** | tick 机制 + provider 输入格式 + 横盘无假利润 |
| **全套** | **331 passed / 0 failed / 51.23s** | —— |

### 为什么两套测试都要有

等价性测试守的是「两条腿一致」，不变量测试守的是「物理定律」。
**只有等价性是不够的**：如果两条腿一致地错（比如都允许负现金），等价性照样全绿。
反过来只有不变量也不够：C++ 可能正确但和 Python 口径不同，回测结论就会漂移。

---

## 3. 三处缺口的验证明细

### 3.1 benchmark 下沉

| 指标 | Python 腿 | C++ 腿 | 差异 |
|---|---|---|---|
| beta | 0.3134530514994872 | 0.3134530514994872 | 0 |
| 成交笔数 | 14 | 14 | 逐笔对齐 |
| 权益点数 | 390 | 390 | 逐点对齐 |

`native_ready()` 不再因 `benchmark is not None` 返回 False。

### 3.2 news_dual_ma 下沉

| 场景 | 结果 |
|---|---|
| 宽松门槛（`min_confidence` 低） | 14 笔成交，`factor()` 调用 **780** 次 |
| 严格门槛（`min_confidence` 高） | 0 笔成交（条件真的生效了） |
| 无 provider | 告警「已剥掉 N 条新闻入场条件」 |

**关键设计**：情绪/置信度条件必须**始终显式写入 spec**，即使取默认值。
否则 spec 退化成纯双均线，`factor()` 零调用，新闻因子形同没接 —— 且不会有任何报错。

### 3.3 tick 架构

| 项 | 实测 |
|---|---|
| 吞吐 | 52000 tick → 243 笔成交、2001 权益点 |
| 卖出费率 | 0.001329 |
| 买入费率 | 0.000304 |
| T+1 | 生效（当日买入不可卖） |
| 现金 | 恒非负 |
| 整手 | 全部对齐 |

卖出费率显著高于买入，差额即印花税 —— 这是判断成本模型是否接对的最快信号。

---

## 4. 本轮抓到的真实 bug（回归测试已覆盖）

### Bug 1：时间轴格式不兼容（端到端必崩）

`SyntheticTickProvider.fetch` 硬读 `df["date"]`，但 `MarketDataService`
把时间放在 **DatetimeIndex**、没有 date 列 → `KeyError: 'date'`。

**为什么测试没抓到**：测试自己构造了带 date 列的 frame，把生产调用形态漏掉了。
这类"测试构造的数据比生产更规整"是最典型的假绿。

修法：`_normalize_frame()` 兼容 DatetimeIndex / date / datetime / dt / time，
统一小写列名，真无时间轴时抛可读 ValueError。

### Bug 2：分钟线被重复展开（数值全错但不报错）

原实现每根 bar 都展开"当天两个交易时段"，于是一根 1 分钟 bar 生成 480 个 tick，
一天被重复展开 240 次，**成交量放大 240 倍**。撮合上限、成本、冲击全部错。

修法：盘中 bar（`(hour,minute)!=(0,0)`）只展开自己那一分钟，日线才展开全天。
实测 14400 根分钟线 → 14400 tick（1:1），成交量守恒。

顺带修：`itertuples` 会把非法标识符列名改写（`open`→`_1`），改位置取列。

---

## 5. 插值可信度（决定 tick 结果能用来干什么）

用 120 个交易日合成分钟线做交叉验证：

| 指标 | 真实 bar | 生成 tick | 一致？ |
|---|---|---|---|
| 首价 | 5.000 | 5.000 | ✓ |
| 末价 | 9.133 | 9.133 | ✓ |
| max/min | 121.209 / 7.9366 | 121.209 / 7.9366 | ✓ |

**结论**：插值不改变价格路径的端点与极值，tick 与 bar 同源同形。
所以收益失真**不是插值算错了**，而是**路径形状被抹平** ——
真实 tick 有跳空、扫单、瞬时流动性枯竭，三段折线重现不了。

→ **合成 tick 只能验机制（撮合时序、T+1、止损触发），不能判断策略优劣。**
这句话必须跟着任何 tick 回测结果一起出现。

### 守门测试

`test_flat_price_returns_only_fees`：常数价格下，收益必须只反映手续费。
防的是插值在横盘上反复踩 high/low 凭空造利润 —— 这种错误在真实行情里
会被涨跌掩盖，只在横盘时暴露，最容易漏。

合理参数下的 sanity check（120 日、标的 ×1.827）：

```
权益 100000 → 119296 (+19.30%)
买入费率 0.000300 / 卖出费率 0.001300
现金恒非负，整手全对齐
```

---

## 6. 删除前的最后检查清单

- [x] 等价性测试 8/8
- [x] 不变量测试 22/22
- [x] tick 测试 21/21
- [x] 全套 331 passed / 0 failed
- [x] 三处缺口（benchmark / news_dual_ma / tick）均已下沉并验证
- [x] 已知局限已在代码与文档双处标注
- [ ] **本报告归档**（正在做）
- [ ] 删除 `_run_python` 与 `NATIVE` 开关

> 最后一项动手前建议再跑一次全量，并 `git tag` 当前状态 ——
> 删完就回不到"两条腿"了，靠 tag 才敢回滚。

---

## 7. 复现方式

```bash
# 1. 建环境（必须 3.12，与 .so 的 cpython-312 对齐）
uv python install 3.12
/home/blacksnow/.local/share/uv/python/cpython-3.12.15-linux-x86_64-gnu/bin/python3 \
    -m venv /tmp/qenv312
/tmp/qenv312/bin/pip install pybind11 numpy pandas setuptools wheel pytest \
    fastapi uvicorn pydantic sqlalchemy alembic cryptography argon2-cffi \
    pyjwt pyotp pyarrow httpx orjson websockets respx pytest-asyncio \
    requests akshare ccxt

# 2. 编译扩展
cd backend
/tmp/qenv312/bin/python setup.py build_ext \
    --build-temp /tmp/qbuild/temp --build-lib /tmp/qbuild/lib
cp /tmp/qbuild/lib/quant/_core*.so quant/

# 3. 验证 ABI（关键，错了就是静默回退）
/tmp/qenv312/bin/python -c "from quant import core_native as c; print(c.native_status())"
# 期望 available: True，python_version: 3.12.x

# 4. 跑测试
/tmp/qenv312/bin/python -m pytest tests/ -q
```

**不要用 venv 默认的 Python 3.13/3.14 跑** —— 会 ImportError 后静默回退，
测试仍能通过但测的是 Python 腿，白测。
