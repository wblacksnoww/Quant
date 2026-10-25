# 仓库协同配置

> 这份文档记录**代码仓里的文件无法表达、只能在 GitHub 网页或 API 上设**的东西。
> 配置文件（CI、模板、CODEOWNERS、Dependabot）已入库，随 git 走；
> 分支保护、合并策略、标签、权限这些不入库，换机器或重开仓库都要重设。
>
> 建议每换一次仓库/加一个人，就对照本文件过一遍。

---

## 一、为什么这些要单独记

仓库里有三类东西：

| 类型 | 存在哪 | 是否随 git 同步 |
|---|---|---|
| CI、Issue/PR 模板、CODEOWNERS、Dependabot、.gitattributes | `.github/`、根目录 | **是** |
| 分支保护、合并策略、required checks | GitHub 仓库设置 | **否** |
| 标签、权限、安全开关 | GitHub 仓库设置 | **否** |

第二、三类如果只存在于某人记忆里，下次重设仓库时会漏掉——于是"代码看着一样，但没人拦住直接往 main 推"。

---

## 二、分支保护（最关键的一项）

路径：**Settings → Branches → Add branch protection rule**

分支名模式：`main`

### 必选项

| 设置 | 值 | 为什么 |
|---|---|---|
| Require a pull request before merging | ✅ | 禁止直推 main。这是唯一能防住 `git push -f` 覆盖他人工作的机制 |
| ↳ Required approvals | `1` | 至少一人看过。只有自己时也保留——强制你走一遍 PR，等于强制自查 |
| ↳ Dismiss stale approvals when new commits are pushed | ✅ | 防止"批准后偷偷加代码" |
| ↳ Require review from Code Owners | ✅ | 让 `CODEOWNERS` 真正生效，而不只是建议 |
| Require status checks to pass | ✅ | CI 红了不能合 |
| ↳ Require branches to be up to date | ✅ | 防止"本地绿、合并后红"（两条分支各自通过，合起来冲突） |
| Require conversation resolution | ✅ | 未回复的 review 意见不能无视 |
| Require linear history | ✅ | 配合 squash merge，主干历史一条 PR 一条提交 |
| Do not allow bypassing the above settings | ✅ | 管理员也受约束。单人开发时这条最容易被关掉，建议保留 |
| Allow force pushes | ❌ | 绝对关闭 |
| Allow deletions | ❌ | 绝对关闭 |

### Required status checks

填写以下 job 名（与 `.github/workflows/ci.yml` 的 `name:` 字段一致）：

```
commit messages
backend
frontend
gateway
```

> `images` 不要列为 required —— 它只在 push 到 main 时运行，PR 上永远不会出现，
> 设为 required 会让所有 PR 永久卡在"等待检查"。

**如果名字填错或拼写不一致，PR 会永远等待一个不存在的检查。** 添加时从下拉列表里选（GitHub 只在检查至少跑过成功一次后才会列出），不要手打。

---

## 三、合并策略

路径：**Settings → General → Pull Requests**

| 设置 | 值 | 理由 |
|---|---|---|
| Allow merge commits | ❌ | 会产生"Merge pull request #12 from ..."这类无信息提交，主干历史变脏 |
| Allow squash merging | ✅ | **主力方式**。一个 PR 一条提交，可读、可 revert、可生成 changelog |
| ↳ Default to pull request title | ✅ | 合并时用 PR 标题作为提交信息，促使标题写规范 |
| Allow rebase merging | ✅ | 例外情况：一个 PR 内含多个逻辑独立改动，想保留分次提交 |
| Automatically delete head branches | ✅ | 省掉手工清理，也避免分支列表越积越多 |
| Always suggest updating pull request branches | ✅ | 提示作者 rebase |

### 关于 squash 与提交规范的关系

用了 squash 之后，PR 里那 20 条 `wip`、`fix typo` **不需要** rebase 干净——它们会被压成一条。所以：

- 作者只需保证 **PR 标题**符合 `<type>(<scope>): <subject>`
- 评审者审的是 diff，不是逐条提交

`.github/workflows/ci.yml` 的 `commit-messages` job 在 PR 上校验的是**分支里的每条提交**。如果你打算全程用 squash，这个检查会显得过严——两种做法：

1. **保留严格检查**（推荐）：促使大家在开发时也写清楚的提交信息，本地 `git log` 才有用。
2. **改为只查 PR 标题**：文件改动很少，但会失去开发阶段的历史可读性。

当前选的是 1。

---

## 四、标签（Labels）

路径：**Issues → Labels**

按「类型 / 优先级 / 状态 / 模块」四组建。用前缀让列表有序。

### 类型

| 标签 | 颜色 | 用途 |
|---|---|---|
| `bug` | `#d73a4a` | 缺陷 |
| `enhancement` | `#a2eeef` | 功能建议 |
| `deps` | `#0366d6` | 依赖升级（Dependabot 自动加） |
| `security` | `#b60205` | 安全问题 |

### 优先级

| 标签 | 颜色 | 含义 |
|---|---|---|
| `P0` | `#b60205` | 回测结论不可信 / 资金计算错误 / 起不来 |
| `P1` | `#d93f0b` | 主要功能不可用 |
| `P2` | `#fbca04` | 有明显绕过方式 |
| `P3` | `#c2e0c6` | 体验改进 |

### 状态

| 标签 | 含义 |
|---|---|
| `needs-triage` | 待确认（新 Issue 自动加） |
| `needs-repro` | 缺复现步骤 |
| `blocked` | 被其他事阻塞 |
| `wontfix` | 不打算做（量化系统里"加错功能比少加危险"，这个标签会常用） |

### 模块（与提交规范的 scope 对齐）

`api` `engine` `strategy` `cpp` `gateway` `tick` `data` `news` `metrics` `risk` `execution` `frontend` `deploy` `ci` `docs`

> 模块标签和 commit scope 用同一套词，好处是「按标签筛 Issue」和「按 scope 筛提交」能对上。

---

## 五、安全设置

路径：**Settings → Code security and analysis**

| 设置 | 建议 | 说明 |
|---|---|---|
| Dependency graph | ✅ | 免费，Dependabot 的前提 |
| Dependabot alerts | ✅ | 有已知 CVE 的依赖会告警 |
| Dependabot security updates | ✅ | 只对**安全**更新自动开 PR（与 `dependabot.yml` 的常规更新不冲突） |
| Secret scanning | ✅ | 公开仓库免费；**这个项目处理券商密钥，必须开** |
| Push protection | ✅ | 在 push 阶段就拦下密钥，而不是事后告警 |
| Code scanning (CodeQL) | 可选 | Python/TS 支持好；C++ 分析较慢 |

### 特别提醒

这个项目会处理：数据库口令、`QUANT_MASTER_KEY`、券商 API key、TOTP 密钥。一旦进了 git 历史，**删掉文件也没用**——历史里还在。真发生了要：

1. 立刻轮换所有相关凭据（先假设已泄露）
2. 用 `git filter-repo` 清理历史并强推
3. 通知所有协作者重新 clone

所以 `.pre-commit-config.yaml` 里加了 `detect-private-key` 和 `check-added-large-files`——本地就拦住最省事。

---

## 六、Actions 权限

路径：**Settings → Actions → General**

| 设置 | 值 | 理由 |
|---|---|---|
| Actions permissions | Allow all actions（或限定官方 + 白名单） | 第三方 action 能读仓库内容 |
| Workflow permissions | **Read repository contents** | `ci.yml` 已显式声明 `permissions`，这里设最小是兜底 |
| Allow GitHub Actions to create PRs | ❌ | 除非要自动化开 PR（Dependabot 不需要这个开关） |

`images` job 需要写 `packages`，它在 job 级单独声明了——这是正确做法：**默认最小，按需提权**。

---

## 七、协作者与权限

路径：**Settings → Collaborators and teams**

| 角色 | 适合谁 | 能力 |
|---|---|---|
| Read | 只看代码 | 读、开 Issue |
| Triage | 参与讨论 | 管理 Issue/PR 标签，不能推代码 |
| Write | 日常开发 | 推分支、开 PR，**不能直推 main**（分支保护拦住） |
| Maintain | 核心维护者 | 改仓库设置（除删除/转移） |
| Admin | 仅限仓库所有者 | 全部 |

**给 Write 就够日常开发了，不要图省事给 Admin。** Admin 能关分支保护，等于绕开所有流程。

多人时建议建 team（如 `@wblacksnoww/core`），`CODEOWNERS` 里写 team 而不是个人——人员变动时不用改文件。

---

## 八、用脚本一次配好（可选）

没有 `gh` CLI 时可以用 API。需要一枚有 `repo` + `admin:repo_hook` 权限的 token：

```bash
export GH_TOKEN=<your_token>
export REPO=wblacksnoww/Quant

curl -fsS -X PUT \
  -H "Authorization: Bearer $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$REPO/branches/main/protection" \
  -d '{
    "required_status_checks": {
      "strict": true,
      "contexts": ["commit messages", "backend", "frontend", "gateway"]
    },
    "enforce_admins": true,
    "required_pull_request_reviews": {
      "required_approving_review_count": 1,
      "dismiss_stale_reviews": true,
      "require_code_owner_reviews": true
    },
    "restrictions": null,
    "required_linear_history": true,
    "allow_force_pushes": false,
    "allow_deletions": false,
    "required_conversation_resolution": true
  }'
```

> 注意：`contexts` 里的名字必须与 CI job 名完全一致，且这些检查**必须先在某个 PR 上跑成功过至少一次**，否则 API 会返回 422（GitHub 拒绝为不存在的检查设保护）。

验证：

```bash
curl -fsS -H "Authorization: Bearer $GH_TOKEN" \
  "https://api.github.com/repos/$REPO/branches/main/protection" | head -40
```

---

## 九、新接手一个人的清单

给新协作者开权限后，让他完成这些，再开始改代码：

- [ ] clone 后 `./scripts/dev.sh` 能起来
- [ ] `python setup.py build_ext --inplace` 能编译，`native_status()` 返回 `available: True`
- [ ] `cd backend && pytest -q` 全绿
- [ ] `pre-commit install --install-hooks && pre-commit install --hook-type commit-msg`
- [ ] 随意写一条 `git commit -m "test"`，**确认被钩子拦下**（验证钩子真的生效了）
- [ ] 开一个只改文档的 PR，确认 CI 四条检查都跑起来
- [ ] 确认自己**不能**直推 main（`git push origin main` 应被拒绝）

最后一条最容易漏。如果新人有 Admin 权限，分支保护对他无效。

---

## 十、定期检查

每季度或每次加人时过一遍：

- [ ] 分支保护规则还在（有人为了"临时推一下"关掉后忘了开）
- [ ] required checks 名字与 `ci.yml` 的 job 名仍然一致（改过 workflow 就容易漂移）
- [ ] `CODEOWNERS` 里的人在组织里仍存在（人走了 GitHub 会静默忽略）
- [ ] Dependabot PR 有在处理，不是积压了 30 个没看
- [ ] `npm audit` / pip 漏洞告警不是长期挂红
