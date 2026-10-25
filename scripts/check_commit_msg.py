#!/usr/bin/env python3
"""提交信息校验：Conventional Commits 规范。

三种用法：

1. commit-msg 钩子（pre-commit 自动调用，失败即拒绝提交）::

       python scripts/check_commit_msg.py --file .git/COMMIT_EDITMSG

2. 校验一段提交信息（手动排查）::

       python scripts/check_commit_msg.py --message "feat(api): 新增 xxx"

3. CI 里校验一个区间的全部提交::

       python scripts/check_commit_msg.py --range origin/main..HEAD

为什么要有这个东西
--------------------
这个仓库的历史提交是两套写法混在一起的：早期用「修复xxx：说明」，
后期用 Conventional Commits。后果是：
  * 没法自动生成 changelog（写脚本分不出哪条是修复、哪条是新功能）
  * 没法按类型筛提交（"这次发版带了哪些 fix"只能人工翻）
  * 想做语义化版本也没有依据（有没有 breaking change 全靠猜）
多人协作时这个问题会放大：每个人的习惯不同，半年后历史就彻底不可读了。

规范本身（简明版）
------------------
::

    <type>(<scope>): <subject>
    │        │         │
    │        │         └─ 一句话说清做了什么（中文可以，句末不加句号）
    │        └─────────── 影响范围，可选，见 SCOPE 白名单
    └──────────────────── 类型，见 TYPES

    [可选正文，空行分隔]

    [可选脚注：BREAKING CHANGE: 说明 / Closes #123]

破坏性变更：类型后加 `!`，如 `feat(api)!: 重构回测接口`，
或在脚注写 `BREAKING CHANGE: ...`。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# --------------------------------------------------------------------------- #
# 规范定义
# --------------------------------------------------------------------------- #

# 类型 → 是否出现在 changelog 的哪个分组（由 changelog 工具按类型分组）
TYPES: dict[str, str] = {
    "feat": "新功能",
    "fix": "缺陷修复",
    "perf": "性能优化",
    "refactor": "重构（不改变外部行为）",
    "docs": "文档",
    "test": "测试",
    "build": "构建系统 / 依赖",
    "ci": "CI 配置",
    "style": "格式（不影响逻辑）",
    "chore": "杂务（版本号、脚手架等）",
    "revert": "回滚",
}

# 允许的 scope。限定白名单是为了让 `git log --grep` 和 changelog 分组稳定；
# 新增模块时在这里登记一行即可。
SCOPES: frozenset[str] = frozenset(
    {
        "api",  # 后端路由 / schemas
        "alembic",  # 数据库迁移
        "engine",  # 回测 / 纸盘 / 实盘引擎
        "strategy",  # 策略、spec、算子
        "cpp",  # C++ 核心（quant._core）
        "gateway",  # C++ 交易网关
        "tick",  # tick 级回测
        "data",  # 数据源、复权、交易日历
        "news",  # 新闻因子
        "metrics",  # 绩效指标
        "risk",  # 风控
        "execution",  # 撮合 / 券商 / 成本
        "frontend",  # 前端
        "deploy",  # Docker / nginx / 部署脚本
        "ci",  # GitHub Actions
        "docs",  # 文档
        "deps",  # 依赖升级
        "test",  # 测试基础设施
    }
)

# 头部长度上限。中文字符按 1 计（不是字节），100 字符对中文约等于
# 100 个汉字，足够说清一件事；再长就该写进正文了。
MAX_HEADER_LEN = 100

# 正文每行宽度建议（超长只警告不报错 —— 中文换行习惯差异大）
MAX_BODY_LEN = 120

_HEADER_RE = re.compile(
    r"^(?P<type>[a-z]+)"
    r"(?:\((?P<scope>[a-z0-9_,\-]+)\))?"
    r"(?P<breaking>!)?"
    r": (?P<subject>.+)$"
)

# 这些提交由 git / GitHub 自动生成，不做规范校验
_EXEMPT_PREFIXES = (
    "Merge ",
    "Revert ",
    "fixup! ",
    "squash! ",
    "amend! ",
    "Initial commit",
)


class Problem:
    """一条校验问题。level: error 会失败，warning 只提示。"""

    __slots__ = ("level", "message")

    def __init__(self, level: str, message: str) -> None:
        self.level = level
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Problem({self.level!r}, {self.message!r})"


def check_message(message: str) -> list[Problem]:
    """校验一条提交信息，返回问题列表（空表示通过）。"""
    problems: list[Problem] = []
    text = message.replace("\r\n", "\n").strip("\n")
    if not text.strip():
        return [Problem("error", "提交信息为空")]

    lines = text.split("\n")
    header = lines[0].strip()

    if any(header.startswith(p) for p in _EXEMPT_PREFIXES):
        return problems

    # 常见错误：把整段说明塞在第一行，没有空行分隔正文
    if len(lines) > 1 and lines[1].strip() and not header.endswith(("。", ".")):
        problems.append(
            Problem(
                "error",
                "首行与正文之间必须有空行；否则 `git log --oneline` 会显示成一大段",
            )
        )

    match = _HEADER_RE.match(header)
    if match is None:
        problems.append(
            Problem(
                "error",
                f"首行不符合 `<type>(<scope>): <subject>` 格式：{header[:60]!r}\n"
                f"  可用类型：{', '.join(sorted(TYPES))}\n"
                f"  例：feat(engine): 支持 tick 级止损\n"
                f"  例：fix(cpp)!: 修正现金闸门判定（破坏性）",
            )
        )
        return problems

    ctype = match.group("type")
    scope = match.group("scope")
    subject = match.group("subject").strip()

    # --- 类型 ---
    if ctype not in TYPES:
        problems.append(
            Problem(
                "error",
                f"未知类型 {ctype!r}，可用：{', '.join(sorted(TYPES))}",
            )
        )

    # --- scope ---
    if scope:
        unknown = [s for s in scope.split(",") if s not in SCOPES]
        if unknown:
            problems.append(
                Problem(
                    "error",
                    f"未知 scope {unknown}，可用：{', '.join(sorted(SCOPES))}",
                )
            )

    # --- subject ---
    if not subject:
        problems.append(Problem("error", "subject 不能为空"))
    else:
        if subject.endswith(("。", ".")):
            problems.append(Problem("warning", "subject 结尾不加句号"))
        if subject[0].isupper() and subject[0].isascii():
            problems.append(Problem("warning", "subject 首字母建议小写（英文时）"))
        if len(subject) < 4:
            problems.append(Problem("warning", f"subject 过短（{len(subject)} 字），说清做了什么"))

    # --- 头部长度 ---
    if len(header) > MAX_HEADER_LEN:
        problems.append(
            Problem(
                "error",
                f"首行 {len(header)} 字，超过 {MAX_HEADER_LEN}；细节请写进正文",
            )
        )

    # --- 破坏性变更必须有说明 ---
    breaking_mark = bool(match.group("breaking"))
    has_breaking_footer = any(
        line.strip().upper().startswith("BREAKING CHANGE:") for line in lines[1:]
    )
    if breaking_mark and not has_breaking_footer:
        problems.append(
            Problem(
                "warning",
                "标题标了 `!` 但正文没有 `BREAKING CHANGE:` 脚注；"
                "升级者不知道要改什么",
            )
        )

    # --- 正文宽度（仅警告） ---
    for i, line in enumerate(lines[1:], start=2):
        if len(line) > MAX_BODY_LEN and not line.strip().startswith(("http", "```")):
            problems.append(Problem("warning", f"第 {i} 行 {len(line)} 字，建议手动换行"))
            break  # 只报一次，避免刷屏

    return problems


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def _commits_in_range(rev_range: str) -> list[tuple[str, str]]:
    """返回 [(sha, message)]，按时间正序。"""
    shas = _git("rev-list", "--reverse", rev_range).split()
    out: list[tuple[str, str]] = []
    for sha in shas:
        out.append((sha, _git("log", "-1", "--format=%B", sha)))
    return out


def _report(sha: str, message: str, problems: list[Problem], *, compact: bool = False) -> bool:
    """打印单条提交的结果，返回是否通过（有 error 即失败）。

    :param compact: 批量校验时用，只打首行 + 第一条错误，避免刷屏
    """
    header = message.strip().split("\n")[0]
    errors = [p for p in problems if p.level == "error"]
    warnings = [p for p in problems if p.level == "warning"]
    if not errors and not warnings:
        return True

    # sha 只截前 8 位；(staged)/(message) 这类标签原样显示
    label = sha[:8] if re.fullmatch(r"[0-9a-f]{7,40}", sha) else sha
    mark = "✗" if errors else "!"
    print(f"{mark} {label} {header[:70]}")

    if compact:
        # 批量模式：每条只报第一条 error 的首行，详细提示留到总结里统一给
        for p in errors[:1]:
            print(f"    error: {p.message.split(chr(10))[0]}")
        return not errors

    for p in errors:
        print(f"    error: {p.message}")
    for p in warnings:
        print(f"    warn : {p.message}")
    return not errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="校验提交信息是否符合 Conventional Commits 规范"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="从文件读取提交信息（commit-msg 钩子用）")
    group.add_argument("--message", "-m", help="直接校验一条提交信息")
    group.add_argument("--range", "-r", help="校验 git 区间内的全部提交，如 origin/main..HEAD")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="warning 也算失败（CI 里建议开启）",
    )
    args = parser.parse_args(argv)

    if args.message is not None:
        problems = check_message(args.message)
        ok = _report("(message)", args.message, problems)
        if args.strict and any(p.level == "warning" for p in problems):
            ok = False
        return 0 if ok else 1

    if args.file:
        try:
            with open(args.file, encoding="utf-8", errors="replace") as fh:
                message = fh.read()
        except OSError as exc:
            print(f"读取提交信息失败：{exc}", file=sys.stderr)
            return 2
        problems = check_message(message)
        ok = _report("(staged)", message, problems)
        if not ok:
            print(
                "\n提示：本次提交被拒绝。改写方式——\n"
                "  git commit -m 'feat(engine): 说明'\n"
                "  或 git commit --amend 修改上一条\n"
                "  规范全文见 CONTRIBUTING.md「提交信息」一节",
                file=sys.stderr,
            )
        if args.strict and any(p.level == "warning" for p in problems):
            ok = False
        return 0 if ok else 1

    try:
        commits = _commits_in_range(args.range or "")
    except subprocess.CalledProcessError as exc:
        print(f"取提交列表失败：{exc.stderr}", file=sys.stderr)
        return 2

    failures = 0
    checked = 0
    for sha, message in commits:
        problems = check_message(message)
        if any(p.level == "warning" for p in problems) and not args.strict:
            problems = [p for p in problems if p.level == "error"]
        checked += 1
        if not _report(sha, message, problems, compact=True):
            failures += 1

    if failures:
        # stdout 是块缓冲、stderr 是无缓冲，不 flush 会让总结跑到详情前面
        sys.stdout.flush()
        print(
            f"\n{failures}/{checked} 条提交不符合规范（区间 {args.range}）。\n"
            f"推荐做法：PR 用 squash merge，合并时把标题改成规范格式 ——\n"
            f"  这样主干历史始终干净，不必回头 rebase 几十条提交。\n"
            f"已在主干的历史不建议重写（会破坏他人本地副本）。\n"
            f"规范：<type>(<scope>): <subject>，如 feat(engine): 支持 tick 级止损\n"
            f"可用类型：{', '.join(sorted(TYPES))}\n"
            f"可用 scope：{', '.join(sorted(SCOPES))}\n"
            f"详见 CONTRIBUTING.md「提交信息」一节。",
            file=sys.stderr,
        )
        return 1
    print(f"✓ {checked} 条提交全部符合规范")
    return 0


if __name__ == "__main__":
    sys.exit(main())
