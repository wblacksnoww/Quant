"""提交信息规范校验器的测试。

校验器本身如果不被测，很容易"规则悄悄放宽了而没人发现" ——
比如某次改动把 scope 白名单改成"任意值"，之后所有提交都能过，
等到需要按类型生成 changelog 时才发现历史没法用。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_commit_msg.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_commit_msg", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    assert _SCRIPT.exists(), f"校验脚本不存在：{_SCRIPT}"
    return _load_module()


def _errors(checker, message: str) -> list[str]:
    return [p.message for p in checker.check_message(message) if p.level == "error"]


def _warnings(checker, message: str) -> list[str]:
    return [p.message for p in checker.check_message(message) if p.level == "warning"]


class TestAcceptsValidMessages:
    @pytest.mark.parametrize(
        "message",
        [
            "feat(engine): 支持 tick 级止损",
            "fix: 修正现金闸门",
            "fix(cpp): 修正跳空穿仓",
            "feat(cpp)!: 重构 spec 编译\n\nBREAKING CHANGE: CompiledSpec 字段重排",
            "docs: 更新 README",
            "refactor(api,frontend): 抽公共组件",
            "perf(metrics): 指标计算改为向量化",
            "test(tick): 补 T+1 不变量",
            "build(deps): 升级 pandas 到 2.2",
            "ci: 加并发取消",
            "chore: 同步版本号",
            "style(frontend): prettier 格式化",
            "revert: feat(engine): 支持 tick",
            "feat: 中文标题完全可以，长度适中就行",
        ],
    )
    def test_no_errors(self, checker, message):
        assert _errors(checker, message) == []

    def test_multiline_with_body_and_footer(self, checker):
        message = (
            "fix(execution): 修正 GTC 挂单冻结资金未释放\n"
            "\n"
            "end_of_day 只处理 DAY 单，GTC 单冻结的资金永不释放，\n"
            "导致策略可用资金逐日枯竭。\n"
            "\n"
            "Closes #42"
        )
        assert _errors(checker, message) == []


class TestRejectsInvalidMessages:
    @pytest.mark.parametrize(
        ("message", "hint"),
        [
            ("修复容器启动失败：CMD 改用 python -m quant.cli", "格式"),
            ("前端：网关页支持选策略", "格式"),
            ("feat 支持 tick", "格式"),
            ("feat:tick", "格式"),
            ("unknown(cpp): 测试", "未知类型"),
            ("feat(badsco): 测试", "未知 scope"),
            ("", "为空"),
        ],
    )
    def test_has_error(self, checker, message, hint):
        errors = _errors(checker, message)
        assert errors, f"应当报错但通过了：{message!r}"
        assert any(hint in e for e in errors), f"错误信息未含 {hint!r}：{errors}"

    def test_header_too_long(self, checker):
        message = "feat(engine): " + "很长的说明" * 20
        errors = _errors(checker, message)
        assert any("超过" in e for e in errors)

    def test_missing_blank_line_before_body(self, checker):
        """首行与正文之间没有空行 —— git log --oneline 会显示成一大段。"""
        message = "feat(engine): 加 tick 回测\n这里直接写正文没有空行"
        errors = _errors(checker, message)
        assert any("空行" in e for e in errors)


class TestWarnings:
    def test_trailing_period_is_warning_not_error(self, checker):
        assert _errors(checker, "feat: 加快回测。") == []
        assert any("句号" in w for w in _warnings(checker, "feat: 加快回测。"))

    def test_short_subject_warns(self, checker):
        assert any("过短" in w for w in _warnings(checker, "feat: 改"))

    def test_breaking_mark_without_footer_warns(self, checker):
        assert any("BREAKING CHANGE" in w for w in _warnings(checker, "feat(cpp)!: 重构接口"))

    def test_breaking_mark_with_footer_ok(self, checker):
        message = "feat(cpp)!: 重构接口\n\nBREAKING CHANGE: 调用方需改参数顺序"
        assert _warnings(checker, message) == []


class TestExemptions:
    @pytest.mark.parametrize(
        "message",
        [
            "Merge branch 'main' into feat/x",
            "Merge pull request #12 from wblacksnoww/dev",
            'Revert "feat(engine): 支持 tick"',
            "fixup! feat(engine): 支持 tick",
            "Initial commit",
        ],
    )
    def test_auto_generated_commits_exempt(self, checker, message):
        assert checker.check_message(message) == []


class TestCli:
    """CLI 行为：退出码是钩子和 CI 唯一的判断依据，必须测。"""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_SCRIPT), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_message_ok_exit_zero(self):
        assert self._run("-m", "feat(engine): 支持 tick").returncode == 0

    def test_message_bad_exit_one(self):
        assert self._run("-m", "改了个东西").returncode == 1

    def test_strict_turns_warning_into_failure(self):
        # 非严格模式：只有 warning，放行
        assert self._run("-m", "feat: 改").returncode == 0
        # 严格模式：warning 也算失败
        assert self._run("-m", "feat: 改", "--strict").returncode == 1

    def test_file_mode_reads_commit_msg(self, tmp_path):
        msg = tmp_path / "COMMIT_EDITMSG"
        msg.write_text("fix(cpp): 修正现金闸门\n", encoding="utf-8")
        assert self._run("--file", str(msg)).returncode == 0

        msg.write_text("随便写的\n", encoding="utf-8")
        assert self._run("--file", str(msg)).returncode == 1

    def test_file_mode_missing_file_exit_two(self, tmp_path):
        assert self._run("--file", str(tmp_path / "nope")).returncode == 2

    def test_rejection_message_mentions_how_to_fix(self, tmp_path):
        """拒绝了必须告诉人怎么改，否则协作时只会让人困惑。"""
        msg = tmp_path / "COMMIT_EDITMSG"
        msg.write_text("改了个东西\n", encoding="utf-8")
        result = self._run("--file", str(msg))
        assert "git commit" in result.stderr or "规范" in result.stderr


class TestGithubWorkflowWiring:
    """CI 与本地必须用同一份规则，否则会出现"本地过、CI 挂"。"""

    def test_ci_calls_the_same_script(self):
        ci = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
        assert ci.exists(), "缺少 CI workflow"
        text = ci.read_text(encoding="utf-8")
        assert "scripts/check_commit_msg.py" in text, (
            "CI 未复用 scripts/check_commit_msg.py，规则会与本地漂移"
        )

    def test_precommit_commit_msg_hook_configured(self):
        cfg = Path(__file__).resolve().parents[2] / ".pre-commit-config.yaml"
        text = cfg.read_text(encoding="utf-8")
        assert "commit-msg" in text, "pre-commit 未配置 commit-msg 阶段钩子"
        assert "check_commit_msg.py" in text, "pre-commit 未复用同一校验脚本"
