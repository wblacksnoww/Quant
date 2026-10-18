"""行情缓存：Parquet 落盘 + 增量刷新 + 多源降级。

两个设计决定值得说明：

1. **前复权数据不做增量合并**。分红送股会让历史价格整体平移，
   只追加新数据会得到"新旧口径拼接"的假历史。因此一旦缓存覆盖不到
   ``end``，就整段重拉，宁可慢一点也不能错。
2. **降级链**：主数据源失败 → 备用源 → 本地缓存 → 明确报错。
   绝不"静默返回空表"，否则回测会得出零交易的假结论。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from ..core.errors import DataError, DataNotFound
from ..core.markets import Instrument, Market, parse_instrument, try_parse
from .base import DataProvider, SymbolInfo, create_provider
from .realtime import RealtimeQuote

log = logging.getLogger(__name__)

_MARKET_PREFIXES = tuple(m.value for m in Market)


class DataStore:
    """按标的缓存日线数据。

    :param root: Parquet 缓存根目录
    :param provider: 主数据源
    :param fallbacks: 备用数据源（按顺序尝试）
    :param legacy_dirs: 兼容旧版 CSV 目录，缓存缺失时作为种子导入
    """

    def __init__(
        self,
        root: str | Path = "data/store",
        provider: DataProvider | None = None,
        fallbacks: Sequence[DataProvider] = (),
        legacy_dirs: Sequence[str | Path] = (),
        adjust: str = "qfq",
    ) -> None:
        base = Path(__file__).resolve().parents[2]
        path = Path(root)
        self.root = path if path.is_absolute() else (base / path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.provider = provider or create_provider("akshare")
        self.fallbacks = tuple(fallbacks)
        self.adjust = adjust
        self.legacy_dirs = [Path(d) if Path(d).is_absolute() else base / d for d in legacy_dirs]
        self._lock = threading.RLock()
        self._migrate_cache_names()

    # ------------------------------------------------------------------ #
    def _parse_cache_name(self, path: Path) -> Instrument | None:
        """``CN_600519_qfq.parquet`` → ``Instrument(CN, 600519)``。"""
        parts = path.stem.split("_")
        if len(parts) >= 2 and parts[0] in _MARKET_PREFIXES:
            try:
                return Instrument(Market(parts[0]), parts[1])
            except ValueError:  # pragma: no cover - 防御
                return None
        return None

    def _migrate_cache_names(self) -> None:
        """旧格式（``600519_qfq.parquet``，无市场前缀，默认 A 股）一次性改名。"""
        for path in sorted(self.root.glob("*.parquet")):
            if self._parse_cache_name(path) is not None:
                continue
            code, _, adjust = path.stem.partition("_")
            if not (code.isdigit() and 1 <= len(code) <= 6):
                continue
            target = self.root / f"CN_{code.zfill(6)}_{adjust or self.adjust or 'raw'}.parquet"
            if target.exists():
                continue
            try:
                path.rename(target)
                log.info("缓存改名（旧格式 → 市场前缀）: %s → %s", path.name, target.name)
            except OSError as exc:  # pragma: no cover
                log.warning("缓存改名失败 %s: %s", path, exc)

    def path_for(self, symbol: str) -> Path:
        inst = parse_instrument(symbol)
        return self.root / f"{inst.market.value}_{inst.code}_{self.adjust or 'raw'}.parquet"

    def has_cache(self, symbol: str) -> bool:
        return self.path_for(symbol).exists()

    def cached_symbols(self) -> list[str]:
        out = []
        for path in self.root.glob("*.parquet"):
            inst = self._parse_cache_name(path)
            if inst is not None:
                out.append(inst.id)
        return sorted(set(out))

    # ------------------------------------------------------------------ #
    def get(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        *,
        refresh: bool = False,
    ) -> pd.DataFrame:
        """取 ``[start, end]`` 日线；缓存命中则不联网。"""
        inst = parse_instrument(symbol)
        start = start or date(2015, 1, 1)
        end = end or date.today()
        if start > end:
            raise ValueError(f"start({start}) 晚于 end({end})")

        with self._lock:
            cached = None if refresh else self._load(inst)
            if cached is not None and self._covers(cached, start, end):
                return self._slice(cached, start, end)

            frame = self._fetch_with_fallback(inst, start, end)
            if frame is None:
                if cached is not None:
                    log.warning(
                        "%s 刷新失败，使用过期缓存（截止 %s）", inst.id, cached["date"].max()
                    )
                    return self._slice(cached, start, end)
                frame = self._seed_from_legacy(inst)
                if frame is None:
                    raise DataError(f"无法获取 {inst.id} 的行情数据，且本地无缓存")

            self._save(inst, frame)
            return self._slice(frame, start, end)

    # ------------------------------------------------------------------ #
    def _load(self, inst: Instrument) -> pd.DataFrame | None:
        path = self.path_for(inst.id)
        if not path.exists():
            return None
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("缓存损坏，忽略 %s: %s", path, exc)
            return None
        if "date" not in frame.columns:
            return None
        frame["date"] = pd.to_datetime(frame["date"])
        return frame

    def _save(self, inst: Instrument, frame: pd.DataFrame) -> None:
        path = self.path_for(inst.id)
        tmp = path.with_suffix(".tmp.parquet")
        frame.to_parquet(tmp, index=False)
        tmp.replace(path)

    @staticmethod
    def _covers(frame: pd.DataFrame, start: date, end: date) -> bool:
        if len(frame) == 0:
            return False
        lo = frame["date"].min().date()
        hi = frame["date"].max().date()
        # 缓存最后一天可能因为当天还没收盘而不含今日，留 3 天缓冲
        return lo <= start and hi >= (end - timedelta(days=3))

    @staticmethod
    def _slice(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
        mask = (frame["date"].dt.date >= start) & (frame["date"].dt.date <= end)
        out = frame.loc[mask].reset_index(drop=True)
        if len(out) == 0:
            raise DataNotFound(f"区间 {start}~{end} 无行情数据")
        return out

    def _fetch_with_fallback(self, inst: Instrument, start: date, end: date) -> pd.DataFrame | None:
        sources: list[DataProvider] = [self.provider, *self.fallbacks]
        errors: list[str] = []
        for source in sources:
            try:
                frame = source.daily(inst.id, start, end, adjust=self.adjust)
                if frame is not None and len(frame) > 0:
                    log.debug("%s 数据来自 %s（%d 行）", inst.id, source.name, len(frame))
                    return frame
            except Exception as exc:  # noqa: BLE001 - 降级链继续
                errors.append(f"{source.name}: {exc}")
                log.warning("数据源 %s 获取 %s 失败: %s", source.name, inst.id, exc)
        if errors:
            log.error("所有数据源均失败（%s）：%s", inst.id, " | ".join(errors))
        return None

    def _seed_from_legacy(self, inst: Instrument) -> pd.DataFrame | None:
        """从旧版 CSV 目录导入一次，避免升级后重复下载全量历史（仅 A 股）。"""
        if inst.market is not Market.CN:
            return None
        for directory in self.legacy_dirs:
            candidate = directory / f"{inst.code}.csv"
            if not candidate.exists():
                continue
            try:
                from .base import normalize_frame

                frame = normalize_frame(pd.read_csv(candidate), symbol=inst.id)
                log.info("从旧缓存导入 %s（%d 行）", candidate, len(frame))
                return frame
            except Exception as exc:  # noqa: BLE001
                log.warning("导入旧缓存失败 %s: %s", candidate, exc)
        return None

    # ------------------------------------------------------------------ #
    def refresh_many(self, symbols: Iterable[str], start: date, end: date) -> dict[str, int]:
        """批量刷新（供后台任务/CLI 使用），返回每个标的的行数。"""
        result: dict[str, int] = {}
        for symbol in symbols:
            try:
                inst = parse_instrument(symbol)
                frame = self.get(inst.id, start, end, refresh=True)
                result[inst.id] = len(frame)
            except Exception as exc:  # noqa: BLE001
                log.error("刷新 %s 失败: %s", symbol, exc)
                result[str(symbol)] = 0
        return result

    def stats(self) -> list[dict]:
        """缓存概览，前端"数据管理"页用。"""
        out = []
        for path in sorted(self.root.glob("*.parquet")):
            inst = self._parse_cache_name(path)
            if inst is None:
                continue
            try:
                frame = pd.read_parquet(path, columns=["date"])
                out.append(
                    {
                        "symbol": inst.id,
                        "code": inst.code,
                        "market": inst.market.value,
                        "file": path.name,
                        "rows": len(frame),
                        "start": frame["date"].min().date().isoformat() if len(frame) else None,
                        "end": frame["date"].max().date().isoformat() if len(frame) else None,
                        "size_kb": round(path.stat().st_size / 1024, 1),
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        return out


class MarketDataService:
    """数据层门面：引擎与 API 都通过它取数，屏蔽缓存/降级细节。"""

    def __init__(self, store: DataStore, provider: DataProvider | None = None) -> None:
        self.store = store
        self.provider = provider or store.provider
        self._info_cache: dict[str, SymbolInfo] = {}
        # 实时快照：短 TTL 抵挡轮询风暴；数据源全挂时回退最近一次成功值
        self._snap_lock = threading.Lock()
        self._snap: dict[str, RealtimeQuote] = {}
        self._snap_ts = 0.0
        self._snap_note = ""
        self._last_good: dict[str, RealtimeQuote] = {}

    def bars(self, symbol: str, start: date, end: date, *, refresh: bool = False) -> pd.DataFrame:
        return self.store.get(symbol, start, end, refresh=refresh)

    def snapshot(
        self, symbols: Iterable[str], *, ttl: float = 3.0
    ) -> tuple[dict[str, RealtimeQuote], str]:
        """实时富快照（含名称/涨跌/量额）。

        返回 ``(quotes, note)``：TTL 内复用上一次结果；数据源失败时回退
        最近一次成功值并标记 ``stale``；``note`` 供前端提示。
        """
        ids: list[str] = []
        for s in symbols:
            text = str(s).strip()
            if not text:
                continue
            inst = try_parse(text)
            if inst is not None and inst.id not in ids:
                ids.append(inst.id)
        if not ids:
            return {}, ""
        now = time.monotonic()
        with self._snap_lock:
            if self._snap and (now - self._snap_ts) < ttl:
                hit = {code: self._snap[code] for code in ids if code in self._snap}
                if hit:
                    return hit, self._snap_note
        try:
            quotes = self.provider.snapshot(ids)
        except NotImplementedError:
            return {}, "当前数据源不支持实时行情"
        except Exception as exc:  # noqa: BLE001
            log.warning("实时行情获取失败: %s", exc)
            with self._snap_lock:
                fallback = {
                    code: replace(self._last_good[code], stale=True)
                    for code in ids
                    if code in self._last_good
                }
            if fallback:
                return fallback, "实时源暂时不可用，展示最近一次成功的快照"
            return {}, "实时源暂时不可用，请稍后重试"
        with self._snap_lock:
            self._snap = quotes
            self._snap_ts = now
            self._last_good.update(quotes)
            missing = [code for code in ids if code not in quotes]
            self._snap_note = f"{len(missing)} 个标的未返回行情" if missing else ""
            return quotes, self._snap_note

    def realtime(self, symbols: Iterable[str]) -> dict[str, float]:
        """仅现价（引擎 live 模式用），共享同一份 TTL 快照。"""
        quotes, _ = self.snapshot(symbols)
        return {code: quote.price for code, quote in quotes.items() if quote.price > 0}

    def search(
        self, keyword: str = "", limit: int = 50, *, market: str | None = None
    ) -> list[SymbolInfo]:
        try:
            return self.provider.search(keyword, limit, market=market)
        except NotImplementedError:
            return self._cached_search(keyword, limit, market)
        except Exception as exc:  # noqa: BLE001
            log.warning("标的检索失败: %s", exc)
            return self._cached_search(keyword, limit, market)

    def _cached_search(self, keyword: str, limit: int, market: str | None) -> list[SymbolInfo]:
        wanted = (market or "").strip().upper() or None
        out: list[SymbolInfo] = []
        for symbol in self.store.cached_symbols():
            inst = try_parse(symbol)
            if inst is None:
                continue
            if wanted and wanted not in ("ALL", "全部") and inst.market.value != wanted:
                continue
            if keyword and keyword.upper() not in inst.id.upper() and keyword.upper() not in inst.code:
                continue
            out.append(SymbolInfo(symbol=inst.id, market=inst.market.value))
            if len(out) >= limit:
                break
        return out

    def info(self, symbol: str) -> SymbolInfo:
        inst = parse_instrument(symbol)
        key = inst.id
        if key not in self._info_cache:
            try:
                items = [
                    i
                    for i in self.provider.search(inst.code, limit=5, market=inst.market.value)
                    if i.symbol == key
                ]
                self._info_cache[key] = items[0] if items else SymbolInfo(symbol=key, market=inst.market.value)
            except Exception:  # noqa: BLE001
                self._info_cache[key] = SymbolInfo(symbol=key, market=inst.market.value)
        return self._info_cache[key]


__all__ = ["DataStore", "MarketDataService"]
