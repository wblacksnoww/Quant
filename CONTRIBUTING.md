# 参与开发

先说清楚这个项目和其他项目最不一样的地方，因为它决定了所有的流程要求。

## 一、为什么这里的规矩比一般项目严

普通项目改错了，最多是**功能坏掉**——会报错，会被发现，会有人来修。

这个项目改错了，可能是**结论悄悄变了而没有任何错误**：

```python
# 看起来完全正常的一行改动
prev_close = close.shift(1)   # 改成 close.shift(2)

# 结果：没有报错，没有警告，测试全绿
# 但回测收益从 18% 变成 34%，你据此调整了实盘参数
```

所以在本项目里，**"跑通了"不等于"对了"**。判断标准是：

> 这个改动会不会让历史回测的结论发生变化？如果会，有没有证据证明新结论是对的？

这就是为什么 PR 模板要求给出"验证证据"而不是"本地跑通了"，也是为什么 C++ 改动有额外的等价性要求。

### 风险分级

改动按风险分四档，档位越高，要求的验证越重：

| 档位 | 涉及路径 | 风险 | 最低要求 |
|---|---|---|---|
| **L0 文档** | `docs/`、`*.md` | 无 | 无 |
| **L1 业务层** | `api/`、`data/`、`frontend/`、CLI | 功能坏掉，会报错 | 测试通过 + 手动验证 |
| **L2 策略表达** | `strategy/`、`spec`、算子 | 结论可能变 | 测试 + 回测结果对照 |
| **L3 计算核心** | `backend/cpp/`、`execution/`、`core/`、`metrics/`、`risk/`、`gateway/` | 结论**静默**变，或资金损失 | L2 全部 + 等价性 + 不变量 + PR 说明影响 |

L3 的改动请务必单独开 PR，不要和功能改动混在一起——混在一起时，评审者无法判断收益变化是"新功能"还是"核心逻辑改了"。

---

## 二、环境搭建

```bash
git clone git@github.com:wblacksnoww/Quant.git
cd Quant

# 一次装好 pre-commit（含提交信息校验）
pip install pre-commit
pre-commit install --install-hooks
pre-commit install --hook-type commit-msg

# 启动（后端热重载 + 前端 Vite）
./scripts/dev.sh
```

`dev.sh` 会自动建 `.venv` 并安装 `backend[dev]`。需要 Python 3.11+、Node 20+。

### C++ 核心必须编译

不编译也能跑，但会走 Python 路径（慢 10~70 倍），**而且你可能不知道自己在测哪条路**。

```bash
cd backend
python setup.py build_ext --inplace
```

验证：

```bash
python -c "from quant import core_native; print(core_native.native_status())"
# 期望：{'available': True, 'python_version': '3.12.x', ...}
```

> ⚠️ **最容易踩的坑：ABI 不匹配是静默失败。**
> `.so` 文件名带 `cpython-312`，只能用 3.12 加载。换成 3.13 会 `ImportError`，
> 而 `core_native` 会**捕获它并回退 Python 路径**——测试照样全绿，
> 但你测的是另一条腿。每次改完 C++ 都要跑一次 `native_status()` 确认。

---

## 三、分支

用短生命周期的特性分支，直接基于 `main`，不做 GitFlow（没有 develop/release 分支——这个项目的发布就是打 tag）。

```bash
git switch main && git pull
git switch -c feat/tick-stop-loss
```

命名：

| 前缀 | 用途 | 例 |
|---|---|---|
| `feat/` | 新功能 | `feat/param-optimize` |
| `fix/` | 缺陷修复 | `fix/gtc-frozen-cash` |
| `refactor/` | 重构 | `refactor/split-schemas` |
| `docs/` | 文档 | `docs/deploy-guide` |
| `chore/` | 杂务 | `chore/bump-deps` |

分支名用英文小写加连字符。中文字符在部分工具（CI 日志、URL、`git branch --sort`）里会有意外行为。

**活不过 3 天**。分支活得越久，合并冲突越痛，而且会偏离 `main` 的语境。做不完就拆成多个 PR，或者用 feature flag 先合一半。

---

## 四、提交信息

格式：`<type>(<scope>): <subject>`

```
feat(engine): 支持 tick 级止损
fix(cpp): 修正跳空时现金闸门判定
docs: 补充部署文档的 HTTPS 说明
refactor(api)!: 重构回测请求体

BREAKING CHANGE: /api/backtest 的 params 字段改名为 strategy_params
```

### 为什么必须统一

不是为了好看。这个项目的历史就是反例：早期一半提交写「修复xxx：说明」，后期改成 `feat:`/`fix:`。后果是：

- **没法自动生成 changelog**：分不出哪条是修复、哪条是新功能
- **没法按类型筛选**：「这次发版带了哪些 fix」只能人工翻几百条
- **没法判断版本号**：有没有 breaking change 全靠猜

### type 与 scope

| type | 用途 | 进 changelog 分组 |
|---|---|---|
| `feat` | 新功能 | 新功能（→ minor） |
| `fix` | 缺陷修复 | 缺陷修复（→ patch） |
| `perf` | 性能优化 | 性能 |
| `refactor` | 重构，外部行为不变 | 重构 |
| `docs` | 文档 | 文档 |
| `test` | 测试 | 不进 |
| `build` | 构建系统、依赖 | 构建 |
| `ci` | CI 配置 | 不进 |
| `style` | 纯格式 | 不进 |
| `chore` | 杂务 | 不进 |
| `revert` | 回滚 | 回滚 |

scope 白名单（完整列表见 `scripts/check_commit_msg.py`）：
`api` `engine` `strategy` `cpp` `gateway` `tick` `data` `news` `metrics` `risk` `execution` `frontend` `deploy` `ci` `docs` `deps` `test`

新增模块时在 `scripts/check_commit_msg.py` 的 `SCOPES` 里加一行。

### 破坏性变更

类型后加 `!`，并在正文写 `BREAKING CHANGE:` 脚注说明**别人要改什么**：

```
refactor(api)!: 回测接口参数扁平化

BREAKING CHANGE: POST /api/backtest 的 params 内嵌字段上移到顶层。
前端调用方需同步修改；旧格式会被拒绝（返回 422）。
```

只标 `!` 不写说明等于没写——升级的人不知道要改哪里。

### 校验

```bash
# 手动检查
python scripts/check_commit_msg.py -m "feat(engine): 支持 tick 级止损"

# 检查一个区间（PR 里会跑）
python scripts/check_commit_msg.py --range origin/main..HEAD
```

pre-commit 的 `commit-msg` 钩子会在提交时自动拦截。CI 用同一个脚本，规则不会漂移。

---

## 五、PR

### 开之前

```bash
git fetch origin
git rebase origin/main        # 变基而不是 merge，保持历史线性
./scripts/dev.sh              # 起来点一遍
cd backend && pytest -q
ruff check backend && ruff format --check backend
cd ../frontend && npm run lint && npm run format:check && npm run build
```

### 大小

**目标 400 行以内**（不含 lock 文件与生成文件）。超过 800 行的 PR 请拆分。

原因很实际：评审者对小 PR 会认真看，对大 PR 会滑到底点"approve"。一个 2000 行的 PR 等于没被评审。

拆法：先合"不改变行为的重构"，再合"行为变更"。这样第二个 PR 的 diff 才是纯粹的语义改动。

### 描述

按 `.github/PULL_REQUEST_TEMPLATE.md` 填。"验证"那一节是评审的主要依据，不要只写"本地跑通了"。

好的验证描述：

> `pytest` 408 passed。新增 `test_flat_price_returns_only_fees`：
> 恒定价格下收益必须只反映手续费。改动前该测试失败（横盘 60 天产生 +12% 虚假收益），
> 改动后通过（−5 元手续费）。

不好的：

> 跑过测试了。

### 合并

用 **squash merge**，合并时把标题改成规范的提交信息。

好处：主干历史一条提交对应一个 PR，可读、可 revert、可自动生成 changelog。
不需要在 PR 里把 20 条 `wip`、`fix typo` 都 rebase 干净——那是在浪费你的时间。

例外：一次修复包含多个逻辑独立的部分时，用 rebase merge 保留分次提交，但每条提交信息都要合规。

### Review

- 作者自查一遍自己的 diff（很多人会发现自己漏了调试代码）
- 至少 1 人 approve 才能合；L3 改动建议 2 人
- **评审者不需要跑起来**：看 diff + 看作者给的验证证据
- 有分歧时不要僵持：先合并"可回滚的最小版本"，用数据说话

---

## 六、测试

### 三层的分工

```
test_invariants.py        不变量：现金≥0、成交≤成交量、T+1、挂单不超龄
        ↑                 守"物理定律"，独立于实现。两边一起错也能抓住
test_native_equivalence.py  等价性：Python 输出 == C++ 输出
        ↑                 守"两条腿一致"。但两边一致地错时它会通过
test_*.py                 单元：具体函数/接口行为
```

**只有等价性是不够的。** 曾经有个跳空穿仓 bug，Python 和 C++ 都允许负现金，等价性测试全绿。是后来补的 `test_cash_never_negative` 才抓住。

新增引擎、策略、优化器时，**先让不变量测试通过**，再谈等价性。

### 什么必须有测试

| 改动 | 必须加测试吗 |
|---|---|
| L3 计算核心 | **必须**，且要不变量 + 等价性各一条 |
| 新增策略/算子 | 必须：至少验证"条件真的起作用"（改门槛 → 结果变） |
| 修 bug | 必须：按"改动前失败、改动后通过"写，防止回归 |
| 新 API | 必须有；鉴权也要覆盖 |
| 前端 | 建议有；至少手动验证步骤写进 PR |
| 文档 | 不需要 |

### 一条经验

**"改门槛、结果不变"的测试比"结果等于某个数字"的测试更有价值。**

前者验证"这个条件真的参与了决策"，后者只验证"我知道现在的输出是啥"——后者在逻辑坏掉时也一样过，只要输出碰巧没变。

```python
# 好：证明条件起作用
def test_strict_threshold_blocks_entries(...):
    loose = run(threshold=0.5)
    strict = run(threshold=1.5)   # 恒不成立
    assert loose.trades and not strict.trades

# 差：只锁定了当前输出
def test_result(...):
    assert result.metrics["total_return"] == pytest.approx(0.1834)
```

### 改了后端 schema，必须重生成前端类型

碰 `quant/server/schemas.py` 或任何 Pydantic 模型之后：

```bash
./scripts/gen-api-types.sh          # 重新生成 openapi.json + schema.ts
git diff -- frontend/src/api/       # 确认 diff 就是你这次改的字段
```

CI 会重新生成一遍再 `git diff --exit-code`，**只要不一致就红**。这不是形式主义：`native` 字段加进 `HealthOut` 后没重生成，CI 就红在这一步——后端有字段、前端类型里没有，`tsc` 反而不会报错（因为它压根不知道这字段存在），最后表现成"界面上某个状态永远是 undefined"。

**踩过一次的坑**：`frontend/src/api/schema.ts` 曾属主是 `root`（历史上以 root 身份生成过），普通用户跑生成脚本会报 `EACCES`，看起来像脚本坏了。修法是先删再生成：

```bash
rm -f frontend/src/api/schema.ts
npx openapi-typescript src/api/openapi.json -o src/api/schema.ts
```

同理，`backend/quant/_core*.so` 也可能属 root，导致新编译产物覆盖失败（静默用着旧二进制）。

**不要给这类生成文件加 `.gitattributes -diff`**。`-diff` 会让 git 当二进制处理，diff 只显示 "Binary files differ"——CI 那个 `git diff --exit-code` 确实还会红，但**评审时看不到契约到底变了什么**，等于把最该被看见的证据藏起来。要折叠噪音用 `linguist-generated`（GitHub 仍默认折叠，但能点开）。

---

## 七、C++ 改动的特别流程

L3 档位，额外要求：

```bash
# 1. 改 cpp/*.cpp 或 cpp/*.hpp
# 2. 重编译
cd backend && python setup.py build_ext --inplace
# 3. 确认真的用上新产物（不看这一步等于白改）
python -c "from quant import core_native; print(core_native.native_status())"
ls -l quant/_core*.so    # mtime 必须是刚才
# 4. 等价性 + 不变量
pytest tests/test_native_equivalence.py tests/test_invariants.py -q
# 5. 全量
pytest -q
```

PR 里必须写：**这次改动会不会改变历史回测的结果？** 如果会，给对照数据。

### 编译产物不入库

`.so` 在 `.gitignore` 里，CI 每次重新编译。本地编译产物属主可能是 `root`（Docker 里编过），此时 `cp` 覆盖会失败，需要先 `rm`。

### 不要为了"顺手"改 C++ 风格

`-O2` 下的数值行为对编译器优化敏感。改循环结构、加 `-ffast-math`、改浮点结合顺序，都可能让回测数值在第 5 位小数上变化。等价性测试用 `abs=1e-6` 容差，超出就是真的改了行为。

---

## 八、代码风格

后端 ruff（行宽 110），前端 eslint + prettier。**不要手写风格约定**，配置就在 `backend/pyproject.toml` 和 `frontend/` 里，pre-commit 会自动跑。

```bash
ruff check backend --fix && ruff format backend
cd frontend && npm run format
```

`ruff format` 的改动请单独提交（`style:` 前缀），不要和逻辑改动混在一起——否则评审者要在格式噪音里找真实改动。

---

## 九、Python 算子（研究通道）

想验证一个新指标时，不必改 C++：用 Python 写算子，秒级迭代。

```python
from quant.strategy.operators import register_operator

@register_operator("vol_adj_mom", period=("int", 20))
def _vam(frame, period=20):
    ret = frame["close"].pct_change()
    vol = ret.rolling(period, min_periods=period).std()
    return ret.rolling(period, min_periods=period).mean() / vol.replace(0, float("nan"))
```

spec 里当字段用：`{"kind": "field", "field": "op:vol_adj_mom(period=20)"}`

三条纪律（违反会让结论不可信）：

1. **必须因果**：第 i 个输出只能用 `src[0..i]`。用 `assert_causal()` 抽查。`shift(-1)`、全样本 `(x - x.mean()) / x.std()` 都是未来函数。
2. **warmup 返回 NaN 不是 0**：C++ 里 `NaN > x` 为 false（条件不成立），而 `0 > x` 可能为 true（假信号）。
3. **研究通道结果不能直接当生产结论**：验证有效后，要在 `cpp/indicators.cpp` 重写并补等价性测试。

---

## 十、发布

```bash
git tag -a v1.2.0 -m "v1.2.0"
git push origin v1.2.0
```

版本号按 Conventional Commits 推导：有 `BREAKING CHANGE` → major；有 `feat` → minor；只有 `fix`/`perf` → patch。

推送 `main` 后 CI 自动构建镜像到 GHCR（`ghcr.io/wblacksnoww/quant-trading-*`）。

---

## 十一、常见坑

| 坑 | 症状 | 处理 |
|---|---|---|
| ABI 不匹配 | 测试全绿但慢 70 倍 | `native_status()` 确认 `available: True` |
| `.so` 没重编译 | 改了 C++ 行为没变 | 看 `.so` mtime |
| 新闻字段缺列 | 回测零成交，无报错 | 看运行日志里的「已剥掉 N 条条件」 |
| 算子未注册 | 回测零成交 | 看日志里的 error 条目 |
| testpaths 是 `backend/tests` | 根目录 `pytest` 找不到测试 | `cd backend && pytest` |
| 合成数据当结论 | 收益好得离谱 | 合成 tick 只能验机制，不能判优劣 |
| CRLF 混入 | 整文件 diff、容器里 `$'\r'` 报错 | `.gitattributes` 已强制 LF，别改编辑器设置 |

---

## 十二、有疑问

- 部署/运维 → `docs/DEPLOY.md`
- 实盘接入 → `docs/LIVE_TRADING.md`
- 架构与计算分层 → `docs/COMPUTE_BOUNDARY.md`
- 安全 → `docs/SECURITY.md`
- 分支保护与仓库设置 → `docs/COLLABORATION.md`

不确定某个改动该不该做，先开 Issue 讨论。**在量化系统里，加错功能比少加功能危险得多**——一个错误的指标会让之后所有回测结论都不可信。
