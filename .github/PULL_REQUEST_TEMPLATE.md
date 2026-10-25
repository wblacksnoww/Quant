## 这个 PR 做了什么

<!-- 一两句话说明。如果是修 bug，先说清"什么现象、为什么发生"。 -->

Closes #

## 类型

<!-- 勾选一个。类型影响 changelog 分组与版本号推导。 -->

- [ ] `feat` 新功能（对应 minor 版本）
- [ ] `fix` 缺陷修复（对应 patch 版本）
- [ ] `perf` 性能优化
- [ ] `refactor` 重构（外部行为不变）
- [ ] `docs` 文档
- [ ] `test` 测试
- [ ] `build` / `ci` 构建与流水线
- [ ] `chore` 杂务
- [ ] **破坏性变更**（需要在下方写明迁移方式）

## 影响面

<!-- 勾选受影响的模块，方便 CODEOWNERS 判断该谁审。 -->

- [ ] 后端 Python 业务层（`backend/quant/{api,data,execution,...}`）
- [ ] **C++ 核心**（`backend/cpp/`，即 `quant._core`）
- [ ] **C++ 交易网关**（`gateway/`）
- [ ] 前端（`frontend/`）
- [ ] 部署 / Docker / CI
- [ ] 数据库模型或迁移（`alembic/`）
- [ ] 仅文档

---

## 验证

<!--
这一节是评审的主要依据。不要只写"本地跑通了"——
给出可复核的证据：具体数字、失败前/失败后的对照、新增测试的名字。
-->

### 我跑了什么

```bash
# 例如
cd backend && pytest -q
ruff check backend && ruff format --check backend
cd frontend && npm run lint && npm run format:check && npm run build
```

结果：

```
粘贴关键输出，例如  331 passed / 0 failed
```

### 本次新增/修改的测试

<!-- 没有测试也要说明为什么不需要（例如纯文档）。 -->

- 

### 手动验证步骤（如适用）

<!-- 涉及 UI 或交互时给出点哪里、看到什么。 -->

1. 
2. 

---

## C++ 改动的额外要求

<!-- 只改动了 backend/cpp/ 或 gateway/ 才需要填，其余可整节删除。 -->

- [ ] 已重新编译扩展：`cd backend && python setup.py build_ext --inplace`
- [ ] 已确认**真的用上了新产物**（`native_status()` 返回 `available: True`，且 `.so` 的 mtime 是刚才）
      <!-- ABI 不匹配时 ImportError 会被吞掉并静默回退 Python 路径，测试照样全绿，但测的是另一条腿 -->
- [ ] 已跑等价性测试：`pytest tests/test_native_equivalence.py`
- [ ] 已跑不变量测试：`pytest tests/test_invariants.py`
- [ ] 若改了撮合 / 成本 / 账户逻辑，已在 PR 描述里写明**对历史回测结论的影响**

## Python 算子（研究通道）改动

<!-- 只在 spec 里用了 op:xxx 字段时需要填。 -->

- [ ] 算子已通过因果性检查（`assert_causal`，未使用 `shift(-1)`、全样本统计量）
- [ ] 明确标注这是研究通道结果，**未**据此修改生产策略参数
- [ ] 若验证有效，已另开 PR 在 `cpp/indicators.cpp` 重写并补等价性测试

---

## 破坏性变更

<!-- 无则写"无"。有则写清：谁受影响、怎么改、是否提供兼容层。 -->

无

## 评审者需要特别注意的地方

<!--
主动指出自己不确定的地方，比等评审者发现更高效。
例如："tick 撮合里 volume_limit 的边界我按 >= 处理，可能与 bar 级的 > 不一致，需要确认。"
-->

- 

## 合并前检查

- [ ] 提交信息符合 `<type>(<scope>): <subject>` 规范
- [ ] 分支已 rebase 到最新 `main`（或确认无冲突）
- [ ] CI 全绿
- [ ] 已自查 diff，没有调试残留（`print`、`console.log`、注释掉的代码）
- [ ] 没有提交密钥、`.env`、本地数据文件
