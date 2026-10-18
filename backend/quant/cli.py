"""命令行入口。

    quant serve                  启动 Web 服务
    quant backtest ...           命令行跑一次回测（不需要浏览器）
    quant fetch ...              预拉行情到本地缓存
    quant keygen                 生成主密钥
    quant doctor                 环境与安全自检
    quant user create ...        创建用户
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:  # 允许 `python -m quant.cli` 直接运行
    sys.path.insert(0, str(BASE))


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


# --------------------------------------------------------------------------- #
# serve
# --------------------------------------------------------------------------- #


def serve(args: argparse.Namespace) -> int:
    import uvicorn

    from quant.config import get_settings

    settings = get_settings()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    _setup_logging(args.verbose)
    uvicorn.run(
        "quant.server.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="debug" if args.verbose else "info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


# --------------------------------------------------------------------------- #
# backtest
# --------------------------------------------------------------------------- #


def backtest(args: argparse.Namespace) -> int:
    from quant.data.store import MarketDataService
    from quant.engine import BacktestEngine, EngineConfig
    from quant.server.context import build_data_service
    from quant.config import get_settings
    from quant.strategy.registry import list_strategies

    _setup_logging(args.verbose)
    settings = get_settings()

    available = {s["id"] for s in list_strategies()}
    if args.strategy not in available:
        print(f"未知策略 {args.strategy!r}，可用：{', '.join(sorted(available))}", file=sys.stderr)
        return 2

    symbols = [s.strip().zfill(6) for s in args.symbols.split(",") if s.strip()]
    end = date.fromisoformat(args.end) if args.end else date.today()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=365 * 3)

    data: MarketDataService = build_data_service(settings)
    params = json.loads(args.params) if args.params else {}
    config = EngineConfig(
        strategy_id=args.strategy,
        symbols=symbols,
        start=start,
        end=end,
        initial_cash=args.cash,
        params=params,
        benchmark=args.benchmark,
        risk=None if args.no_risk else None,
    )
    engine = BacktestEngine(config, data)
    engine.on_progress(lambda p, m: print(f"\r[{p * 100:5.1f}%] {m[:60]:<60}", end="", flush=True))
    result = engine.run()
    print()

    if result.status == "failed":
        print(f"回测失败: {result.error}", file=sys.stderr)
        return 1

    metrics = result.metrics.to_dict()
    print("\n====== 回测结果 ======")
    print(f"策略        {result.strategy_id}")
    print(f"区间        {metrics['start']} ~ {metrics['end']}")
    print(f"初始/期末   {metrics['initial_equity']:,.0f} → {metrics['final_equity']:,.0f}")
    print(f"总收益      {metrics['total_return']:.2%}")
    print(f"年化收益    {metrics['annual_return']:.2%}")
    print(f"夏普/索提诺 {metrics['sharpe']:.2f} / {metrics['sortino']:.2f}")
    print(f"最大回撤    {metrics['max_drawdown']:.2%}（持续 {metrics['max_drawdown_days']} 个交易日）")
    print(f"胜率        {metrics['win_rate']:.1%}（{metrics['trade_count']} 笔平仓）")
    print(f"总费用      {metrics['total_fees']:,.2f}")

    if args.output:
        Path(args.output).write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完整结果已写入 {args.output}")
    return 0


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #


def fetch(args: argparse.Namespace) -> int:
    from quant.config import get_settings
    from quant.server.context import build_data_service

    _setup_logging(args.verbose)
    settings = get_settings()
    service = build_data_service(settings)
    symbols = [s.strip().zfill(6) for s in args.symbols.split(",") if s.strip()]
    end = date.fromisoformat(args.end) if args.end else date.today()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=365 * args.years)
    print(f"拉取 {len(symbols)} 个标的：{start} ~ {end}，数据源 {service.provider.name}")
    result = service.store.refresh_many(symbols, start, end)
    for symbol, rows in result.items():
        flag = "OK " if rows else "FAIL"
        print(f"  [{flag}] {symbol}: {rows} 行")
    return 0 if all(result.values()) else 1


# --------------------------------------------------------------------------- #
# keygen / doctor / user
# --------------------------------------------------------------------------- #


def keygen(args: argparse.Namespace) -> int:
    from quant.security.crypto import generate_key_b64

    key = generate_key_b64()
    print("主密钥（32 字节，base64url）:\n")
    print(f"  {key}\n")
    print("用法：")
    print(f"  export QUANT_MASTER_KEY='{key}'")
    print("  或写入文件后： export QUANT_MASTER_KEY_FILE=/path/to/master.key\n")
    print("⚠ 请立即备份到密码管理器/KMS。密钥丢失 = 所有已加密的凭据与 TOTP 种子永久无法恢复。")
    if args.file:
        path = Path(args.file)
        path.write_text(key, encoding="utf-8")
        os.chmod(path, 0o600)
        print(f"已写入 {path}（权限 600）")
    return 0


def doctor(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    ok = True
    print("== 量化系统自检 ==\n")

    # Python 与依赖
    import platform

    print(f"Python          {platform.python_version()}")
    for module, label in [
        ("fastapi", "FastAPI"),
        ("sqlalchemy", "SQLAlchemy"),
        ("pandas", "pandas"),
        ("cryptography", "cryptography"),
        ("argon2", "argon2-cffi"),
        ("pyotp", "pyotp"),
        ("akshare", "akshare"),
    ]:
        try:
            __import__(module)
            print(f"{label:<15} OK")
        except Exception as exc:  # noqa: BLE001
            print(f"{label:<15} 缺失 ({exc})")
            ok = False

    # 密钥
    try:
        from quant.security.crypto import master_key

        mk = master_key()
        print(f"\n主密钥来源      {mk.source}")
        print(f"密钥指纹        {mk.fingerprint}")
        if mk.source.startswith("generated"):
            print("  ⚠ 当前为自动生成的开发密钥，生产环境请改用 Docker secret / KMS")
    except Exception as exc:  # noqa: BLE001
        print(f"主密钥          FAIL: {exc}")
        ok = False

    # 数据库
    try:
        from quant.config import get_settings
        from quant.storage.db import Database

        db = Database(get_settings().database_url)
        print(f"数据库          {'OK' if db.healthcheck() else 'FAIL'} ({db.url.split(':')[0]})")
        ok = ok and db.healthcheck()
        db.dispose()
    except Exception as exc:  # noqa: BLE001
        print(f"数据库          FAIL: {exc}")
        ok = False

    # 策略与数据源
    from quant.data.base import available_providers
    from quant.strategy.registry import strategy_ids

    print(f"策略            {', '.join(strategy_ids())}")
    print(f"数据源          {', '.join(available_providers())}")

    print("\n结果:", "✅ 全部通过" if ok else "❌ 存在问题，请检查上面的输出")
    return 0 if ok else 1


def user(args: argparse.Namespace) -> int:
    from quant.config import get_settings
    from quant.security.password import hash_password, validate_strength
    from quant.storage.db import Database
    from quant.storage.repositories import UserRepository

    _setup_logging(args.verbose)
    if args.action != "create":
        print("目前仅支持 create", file=sys.stderr)
        return 2
    try:
        validate_strength(args.password)
    except Exception as exc:  # noqa: BLE001
        print(f"密码强度不足: {exc}", file=sys.stderr)
        return 2

    db = Database(get_settings().database_url)
    db.create_all()
    with db.session() as session:
        repo = UserRepository(session)
        if repo.get_by_username(args.username):
            print("用户名已存在", file=sys.stderr)
            return 1
        created = repo.create(args.username, hash_password(args.password), role=args.role)
        print(f"已创建用户 {created.username}（role={created.role}, id={created.id}）")
    db.dispose()
    return 0


# --------------------------------------------------------------------------- #
# 参数解析
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant", description="A 股量化交易系统 CLI")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="启动 Web 服务")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--reload", action="store_true", help="代码热重载（开发用）")
    p.add_argument("--workers", type=int, default=1)
    p.set_defaults(func=serve)

    p = sub.add_parser("backtest", help="命令行回测")
    p.add_argument("--strategy", required=True)
    p.add_argument("--symbols", required=True, help="逗号分隔，如 600519,000001")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--cash", type=float, default=1_000_000)
    p.add_argument("--params", help="策略参数 JSON，如 '{\"fast\":10,\"slow\":30}'")
    p.add_argument("--benchmark", help="基准标的代码")
    p.add_argument("--no-risk", action="store_true")
    p.add_argument("--output", "-o", help="把完整结果写入 JSON 文件")
    p.set_defaults(func=backtest)

    p = sub.add_parser("fetch", help="预拉行情缓存")
    p.add_argument("--symbols", required=True)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--years", type=int, default=5)
    p.set_defaults(func=fetch)

    p = sub.add_parser("keygen", help="生成主密钥")
    p.add_argument("--file", help="同时写入文件")
    p.set_defaults(func=keygen)

    p = sub.add_parser("doctor", help="环境与安全自检")
    p.set_defaults(func=doctor)

    p = sub.add_parser("user", help="用户管理")
    p.add_argument("action", choices=["create"])
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--role", choices=["user", "admin"], default="user")
    p.set_defaults(func=user)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
