"""仓库协同配置的防漂移测试。

这些文件（CI、模板、CODEOWNERS、Dependabot、.gitattributes）不会被任何
业务代码 import，所以它们坏掉时**没有任何测试会报错** —— 直到某天发现
"CI 没拦住"，或者"PR 永远卡在等待一个不存在的检查"。

`docs/COLLABORATION.md` 里已经提醒过这个风险（§10 定期检查）。
与其靠人记得，不如让测试守住：
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GITHUB = ROOT / ".github"


@pytest.fixture(scope="module")
def ci() -> dict:
    path = GITHUB / "workflows" / "ci.yml"
    assert path.exists(), "缺少 CI workflow"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestCiWorkflow:
    def test_yaml_valid_and_has_jobs(self, ci):
        assert "jobs" in ci and ci["jobs"], "CI 没有定义任何 job"

    def test_expected_jobs_exist(self, ci):
        """job 名是分支保护里 required checks 的锚点，改名会悄悄失效。"""
        assert set(ci["jobs"]) >= {"commit-messages", "backend", "frontend", "gateway", "images"}

    def test_job_names_are_set_explicitly(self, ci):
        """GitHub 的 required check 用的是 job 的 `name:` 字段。

        如果只写 job id 不写 name，界面显示的是 id；一旦有人改了 id，
        分支保护里配的检查名就对不上了，PR 会永远"等待中"。
        """
        for job_id, job in ci["jobs"].items():
            assert "name" in job, f"job {job_id} 没有显式 name，required checks 会随 id 改名而失效"

    def test_has_concurrency_cancel(self, ci):
        """多人协作时没有并发取消会白白烧 CI 额度。"""
        assert "concurrency" in ci
        assert "cancel-in-progress" in ci["concurrency"]

    def test_permissions_least_privilege(self, ci):
        """默认权限必须是最小的；需要写的 job 自己声明。"""
        assert ci.get("permissions", {}).get("contents") == "read"
        # images 要推镜像，必须单独要 packages: write
        assert ci["jobs"]["images"]["permissions"].get("packages") == "write"

    def test_images_only_on_main_push(self, ci):
        cond = ci["jobs"]["images"].get("if", "")
        assert "refs/heads/main" in cond, "镜像不应在 PR 上构建"

    def test_images_waits_for_all_checks(self, ci):
        needs = set(ci["jobs"]["images"].get("needs", []))
        assert needs >= {"backend", "frontend", "gateway"}, "镜像不能在检查未通过时构建"

    def test_commit_messages_job_uses_single_script(self, ci):
        """CI 与 pre-commit 必须复用同一个脚本，否则规则会漂移。"""
        job = ci["jobs"]["commit-messages"]
        steps_text = yaml.safe_dump(job)
        assert "scripts/check_commit_msg.py" in steps_text

    def test_backend_verifies_openapi_types_in_sync(self, ci):
        """后端 schema 改了但前端类型没重生成，必须红。

        `tsc` 抓不到这种漂移——它按 schema.ts 检查，而后端新加的字段
        在 schema.ts 里不存在，于是"字段永远 undefined"能一路绿灯到生产。
        唯一有效的手段是重新生成再比对。
        """
        steps = ci["jobs"]["backend"]["steps"]
        dumped = yaml.safe_dump(steps)
        assert "gen-api-types.sh" in dumped, "backend job 缺少 OpenAPI 类型生成步骤"
        assert "git diff --exit-code" in dumped, "生成后必须比对 diff，否则漂移不会被发现"

    def test_pr_checkout_uses_head_not_merge_commit(self, ci):
        """PR 事件默认 checkout 合并提交，会让提交区间算错。"""
        steps = ci["jobs"]["commit-messages"]["steps"]
        checkout = next(s for s in steps if "checkout" in str(s.get("uses", "")))
        ref = str(checkout.get("with", {}).get("ref", ""))
        assert "pull_request.head.sha" in ref, (
            "commit-messages job 应 checkout PR 的真实 head，否则 HEAD 的祖先含 main，区间计算会混入无关提交"
        )

    def test_fetch_depth_zero_for_range_check(self, ci):
        steps = ci["jobs"]["commit-messages"]["steps"]
        checkout = next(s for s in steps if "checkout" in str(s.get("uses", "")))
        assert checkout.get("with", {}).get("fetch-depth") == 0, "区间校验需要完整历史，fetch-depth: 0 不能省"


class TestRequiredChecksDocMatchesCi:
    """COLLABORATION.md 里写的 required checks 必须与 ci.yml 的 job name 一致。

    这是最容易漂移的一处：改了 workflow 的 name 字段，文档不跟着改，
    照着文档去配分支保护就会配出一个"永远等待"的检查。
    """

    @staticmethod
    def _doc_text() -> str:
        doc = ROOT / "docs" / "COLLABORATION.md"
        assert doc.exists(), "缺少协同配置文档"
        return doc.read_text(encoding="utf-8")

    def test_all_ci_job_names_documented(self, ci):
        text = self._doc_text()
        for job in ci["jobs"].values():
            name = job["name"]
            # images 只在 main push 运行，不列为 required，允许不出现
            if name == "images":
                continue
            assert name in text, f"文档未列出 required check {name!r}，照着配会漏"

    def test_doc_does_not_invent_checks(self, ci):
        """反方向：文档里不能出现 ci.yml 中不存在的检查名。"""
        text = self._doc_text()
        real_names = {job["name"] for job in ci["jobs"].values()}
        # 文档里那段代码块列出的检查
        block_start = text.find("### Required status checks")
        assert block_start > 0
        block = text[block_start : block_start + 600]
        code = block.split("```")[1] if "```" in block else ""
        listed = [line.strip() for line in code.splitlines() if line.strip()]
        assert listed, "文档的 Required status checks 段落没有列出任何检查名"
        for name in listed:
            assert name in real_names, f"文档列了不存在的检查 {name!r}，PR 会永远等待"


class TestCommunityFiles:
    def test_pull_request_template(self):
        path = GITHUB / "PULL_REQUEST_TEMPLATE.md"
        assert path.exists(), "缺少 PR 模板"
        text = path.read_text(encoding="utf-8")
        # 模板的价值在于强制给出证据，没有这两节就退化成空壳
        assert "验证" in text, "PR 模板缺少「验证」章节"
        assert "C++" in text, "PR 模板缺少 C++ 改动的等价性要求"

    @pytest.mark.parametrize("name", ["bug_report.yml", "feature_request.yml", "config.yml"])
    def test_issue_templates_exist_and_parse(self, name):
        path = GITHUB / "ISSUE_TEMPLATE" / name
        assert path.exists(), f"缺少 Issue 模板 {name}"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_issue_forms_have_required_fields(self):
        for name in ("bug_report.yml", "feature_request.yml"):
            data = yaml.safe_load((GITHUB / "ISSUE_TEMPLATE" / name).read_text(encoding="utf-8"))
            assert data.get("name") and data.get("description")
            body = data.get("body") or []
            assert len(body) >= 4, f"{name} 字段过少，收集不到排查信息"
            ids = [item.get("id") for item in body if item.get("id")]
            assert len(ids) == len(set(ids)), f"{name} 的字段 id 有重复"

    def test_codeowners_covers_high_risk_paths(self):
        """最高风险的目录必须有明确 owner，否则改动无人可指派。"""
        path = GITHUB / "CODEOWNERS"
        assert path.exists(), "缺少 CODEOWNERS"
        text = path.read_text(encoding="utf-8")
        for risky in ("/backend/cpp/", "/gateway/", "/backend/quant/execution/", "/deploy/"):
            assert risky in text, f"CODEOWNERS 未覆盖高风险路径 {risky}"

    def test_contributing_exists_and_states_commit_convention(self):
        path = ROOT / "CONTRIBUTING.md"
        assert path.exists(), "缺少 CONTRIBUTING.md"
        text = path.read_text(encoding="utf-8")
        assert "<type>(<scope>): <subject>" in text, "未写明提交信息格式"
        assert "不变量" in text, "未说明不变量测试的要求"


class TestDependabot:
    def test_valid_and_covers_ecosystems(self):
        data = yaml.safe_load((GITHUB / "dependabot.yml").read_text(encoding="utf-8"))
        assert data["version"] == 2
        ecosystems = {u["package-ecosystem"] for u in data["updates"]}
        assert ecosystems >= {"pip", "npm", "github-actions"}

    def test_numeric_libs_not_auto_minor(self):
        """pandas / numpy 的小版本升级会改变回测数值，不能自动提 PR。

        这是本仓库特有的约束：CI 只验证"不崩"，不验证"结论没变"。
        """
        data = yaml.safe_load((GITHUB / "dependabot.yml").read_text(encoding="utf-8"))
        pip_update = next(u for u in data["updates"] if u["package-ecosystem"] == "pip")
        ignored = {i["dependency-name"] for i in pip_update.get("ignore", [])}
        assert "pandas" in ignored
        assert "numpy" in ignored

    def test_major_upgrades_blocked_by_wildcard(self):
        """前端与 pip 必须用通配符拦掉**所有** major 升级，而不是逐个列举。

        曾经只能写 `react` / `react-dom` 两个，第一次运行就漏掉了
        vite 5.4 → 8.3 和 echarts 5.6 → 6.1 两个跨大版本提案。
        逐个列举的方式每加一个库就要重新判断一次，必然漏。

        这类升级 CI 抓不到：vite 大版本可能编译通过但产物行为变了，
        ECharts 大版本可能类型对得上但某个 option 运行时静默失效。
        """
        data = yaml.safe_load((GITHUB / "dependabot.yml").read_text(encoding="utf-8"))
        for ecosystem in ("npm", "pip"):
            update = next(u for u in data["updates"] if u["package-ecosystem"] == ecosystem)
            wildcards = [
                i
                for i in update.get("ignore", [])
                if i.get("dependency-name") == "*"
                and "version-update:semver-major" in i.get("update-types", [])
            ]
            assert wildcards, (
                f"{ecosystem} 没有用 `dependency-name: *` 拦住 major 升级；逐个列举依赖名会漏掉跨大版本提案"
            )


class TestGitAttributes:
    def test_exists_and_forces_lf_on_scripts(self):
        """Shell 脚本带 CRLF 会在容器里报 `$'\\r': command not found`。"""
        path = ROOT / ".gitattributes"
        assert path.exists(), "缺少 .gitattributes，跨平台协作会产生换行符噪音"
        text = path.read_text(encoding="utf-8")
        assert "eol=lf" in text
        assert "*.sh" in text

    def test_marks_binaries(self):
        text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        for ext in ("*.png", "*.so"):
            assert ext in text, f".gitattributes 未把 {ext} 标记为二进制"

    def test_generated_files_keep_visible_diff(self):
        """生成文件不能用 `-diff`。

        `-diff` 让 git 把文件当二进制，diff 只显示 "Binary files differ"。
        CI 的 `git diff --exit-code` 仍然会红，但**评审看不到契约变了什么**。

        这曾经真的发生过：给 schema.ts 加 `-diff` 本意是折叠噪音，
        结果后端改字段、前端类型没跟上时，PR 里一片 "Binary files differ"，
        最该被看见的证据被藏起来了。折叠噪音应该用 `linguist-generated`。
        """
        text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        for generated in (
            "frontend/src/api/schema.ts",
            "frontend/src/api/openapi.json",
            "frontend/package-lock.json",
            "deploy/requirements.lock.txt",
        ):
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or not stripped.startswith(generated):
                    continue
                assert "-diff" not in stripped, (
                    f"{generated} 被标了 `-diff`，生成内容的变更将不可审查；要折叠噪音请用 linguist-generated"
                )


class TestPreCommit:
    def test_has_commit_msg_hook(self):
        text = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        assert "stages: [commit-msg]" in text or "commit-msg" in text
        assert "check_commit_msg.py" in text

    def test_has_private_key_detection(self):
        """这个项目处理券商密钥，本地拦截比事后清理省事得多。"""
        text = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        assert "detect-private-key" in text
