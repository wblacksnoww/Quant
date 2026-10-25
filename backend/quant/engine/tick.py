"""tick 级回测的 Python 侧：数据供给 + 结果封装。

## 分层

* **C++ 负责**：逐 tick 循环、重采样、信号求值、撮合、账户记账。
  整个回测在**一次** C++ 调用里跑完，不逐 tick 回调 Python。
* **Python 负责**：把 tick 数据准备成列式数组、把结果包装成 EngineResult。
  这两件事都是 O(1) 次跨语言调用，不是热路径。

## 为什么 tick 循环必须在 C++ 里整段完成

一天 A 股约 4800 个 tick（3 秒一笔，4 小时），十年 1200 万条。
如果每个 tick 都 pybind 往返一次（约 1~2 μs），光跨语言开销就是
12~24 秒 —— 比纯 Python 循环还慢。所以 `run_tick_backtest` 的设计是
"进一次、出一次"，中间全在 C++ 里。

## tick 数据从哪来

`TickProvider` 是协议；当前实现：

* `SyntheticTickProvider` —— 从分钟线插值生成。**不是真实 tick**，
  用途是把架构跑通、验证撮合与时序纪律。插值出的价格路径是平滑的，
  不会重现真实 tick 的跳空与扫单，所以**不能用它的回测结果做策略决策**。
* 将来接 Level2：实现同一个 `TickProvider.fetch()` 即可，
  C++ 引擎与上层代码都不用改。

这个限制是刻意的：宁可给一个标注清楚的模拟源，也不假装它是真实数据。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

NS_PER_SEC = 1_000_000_000


@dataclass
class TickFrame:
    """单标的的 tick 列式数据（直接映射到 C++ TickSeries）。"""

    symbol: str
    ts: np.ndarray       # int64, ns since epoch
    price: np.ndarray    # float64
    bid: np.ndarray
    ask: np.ndarray
    volume: np.ndarray
    amount: np.ndarray

    def __post_init__(self) -> None:
        n = len(self.ts)
        for name in ("price", "bid", "ask", "volume", "amount"):
            arr = getattr(self, name)
            if len(arr) != n:
                raise ValueError(f"{self.symbol}.{name} 长度 {len(arr)} 与 ts 长度 {n} 不一致")

    @property
    def size(self) -> int:
        return len(self.ts)

    def slice_range(self, start: date, end: date) -> "TickFrame":
        """按日期切片（ts 是 ns，先转成日期边界再比较）。"""
        lo = int(pd.Timestamp(datetime.combine(start, datetime.min.time())).value)
        hi = int(pd.Timestamp(datetime.combine(end, datetime.max.time())).value)
        mask = (self.ts >= lo) & (self.ts <= hi)
        idx = np.flatnonzero(mask)
        if len(idx) == 0:
            return TickFrame(self.symbol, *[np.array([], dtype=t) for t in (
                np.int64, np.float64, np.float64, np.float64, np.float64, np.float64)])
        return TickFrame(
            self.symbol,
            self.ts[idx], self.price[idx], self.bid[idx],
            self.ask[idx], self.volume[idx], self.amount[idx],
        )


class TickProvider(Protocol):
    """tick 数据源协议。

    接入真实 Level2 时实现这个接口即可 —— 引擎与上层代码都不需要改。
    """

    def fetch(self, symbol: str, start: date, end: date) -> TickFrame:
        ...

    @property
    def is_synthetic(self) -> bool:
        """数据是否为模拟生成（供上层在 UI / 报告里标注）。"""
        ...


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """把各种来源的 K 线规整成含 `date` 列的 DataFrame。

    三个来源的时间轴放法都不一样，不兼容会直接 KeyError：
    * `MarketDataService.bars()` / `minute_bars()` → DatetimeIndex，无 date 列
    * 部分 provider 返回 → 有 `date` 列
    * 交易所导出 → 有 `dt` / `time` 列

    列名也不可靠：有些源用 `Open`/`Close` 大写。这里统一成小写。
    """
    df = frame.copy()

    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
        # reset_index 后列名可能是 index / date / datetime，取第一个时间列
        time_col = next(
            (c for c in df.columns if c in ("date", "datetime", "index", "level_0")), None
        )
        if time_col is None:
            time_col = df.columns[0]
        df = df.rename(columns={time_col: "date"})
    elif "date" in df.columns:
        pass
    elif "datetime" in df.columns:
        df = df.rename(columns={"datetime": "date"})
    elif "dt" in df.columns:
        df = df.rename(columns={"dt": "date"})
    elif "time" in df.columns:
        df = df.rename(columns={"time": "date"})
    else:
        raise ValueError(
            f"无法确定时间轴：index 是 {type(frame.index).__name__}，"
            f"列为 {list(frame.columns)}；需要 DatetimeIndex 或 date/datetime/dt/time 列"
        )

    df["date"] = pd.to_datetime(df["date"])
    df = df.rename(columns={c: str(c).lower() for c in df.columns})
    for need in ("open", "close"):
        if need not in df.columns:
            raise ValueError(f"K 线缺少 {need} 列，实际列：{list(df.columns)}")
    return df.reset_index(drop=True)


@dataclass
class SyntheticTickProvider:
    """从分钟线插值生成 tick —— **模拟数据，仅供架构验证**。

    生成规则：
    * 每个交易日的交易时段（09:30-11:30, 13:00-15:00）按 `interval_seconds` 切分；
    * 价格在相邻分钟 OHLC 之间按线性插值 + 微小噪声；
    * 成交量按 tick 数均分分钟成交量。

    局限（重要）：
    * 真实 tick 有跳跃、扫单、瞬时流动性枯竭，插值完全无法重现；
    * 因此**不要用它得出的盈亏做策略判断** —— 它的价值在于验证
      "撮合时序对不对""T+1 是否生效""止损是否在 tick 级触发"。
    """

    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    interval_seconds: int = 60
    noise_bps: float = 0.0

    is_synthetic: bool = True

    def fetch(self, symbol: str, start: date, end: date) -> TickFrame:
        frame = self.frames.get(symbol)
        if frame is None or not len(frame):
            empty_i = np.array([], dtype=np.int64)
            empty_f = np.array([], dtype=np.float64)
            return TickFrame(symbol, empty_i, empty_f, empty_f, empty_f, empty_f, empty_f)

        df = _normalize_frame(frame)
        df = df[(df["date"].dt.date >= start) & (df["date"].dt.date <= end)]
        if not len(df):
            empty_i = np.array([], dtype=np.int64)
            empty_f = np.array([], dtype=np.float64)
            return TickFrame(symbol, empty_i, empty_f, empty_f, empty_f, empty_f, empty_f)

        ts_list: list[int] = []
        px_list: list[float] = []
        vol_list: list[float] = []

        step = max(1, int(self.interval_seconds))
        rng = np.random.default_rng(abs(hash(symbol)) % (2**32))

        # 用位置取列而不是 itertuples：itertuples 会把非法标识符列名改写
        # （open → _1），依赖 row.open 会在脏列名上静默取错值。
        col = {k: df[k].to_numpy() for k in ("date", "open", "close")}
        for opt, default in (("high", None), ("low", None), ("volume", 0.0)):
            col[opt] = df[opt].to_numpy() if opt in df.columns else None

        for i in range(len(df)):
            ts_i = pd.Timestamp(col["date"][i])
            day = ts_i.date()
            o = float(col["open"][i])
            c = float(col["close"][i])
            h = float(col["high"][i]) if col["high"] is not None else c
            lo = float(col["low"][i]) if col["low"] is not None else o
            vol = float(col["volume"][i]) if col["volume"] is not None else 0.0
            if not (np.isfinite([o, c, h, lo]).all() and o > 0 and c > 0):
                continue  # 脏 bar（停牌/未上市）跳过，否则会污染插值路径

            # 这根 bar 覆盖的时间窗。
            #
            # **日线**：展开成当天两个交易时段（09:30-11:30 / 13:00-15:00）。
            # **分钟线**：只展开它自己那一分钟 —— 否则每根 1 分钟 bar 都会生成
            #   整天的 tick，一天被重复展开 240 次，成交量也放大 240 倍。
            #
            # 时刻用 HHMM 表示，**不能直接相减算分钟数**（1130-930=200
            # 会算出 200 分钟，而实际只有 120 分钟），必须先转成"当日第几分钟"。
            intraday = (ts_i.hour, ts_i.minute) != (0, 0)
            if intraday:
                segs = [(ts_i.hour, ts_i.minute, self.interval_seconds)]
            else:
                segs = []
                for seg_start, seg_end in ((930, 1130), (1300, 1500)):
                    start_min = (seg_start // 100) * 60 + seg_start % 100
                    end_min = (seg_end // 100) * 60 + seg_end % 100
                    segs.append((seg_start // 100, seg_start % 100, end_min - start_min))

            for hh, mm, span_min in segs:
                if intraday:
                    n_ticks = 1  # 一根分钟 bar 自己就是一档
                else:
                    n_ticks = max(1, (span_min * 60) // step)
                base = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
                # 价格路径：open → high → close（三段折线）
                for k in range(n_ticks):
                    frac = k / max(1, n_ticks - 1)
                    if frac < 0.5:
                        p = o + (h - o) * (frac / 0.5)
                    else:
                        p = h + (c - h) * ((frac - 0.5) / 0.5)
                    p = max(lo, min(h, p)) if h >= lo else p
                    if self.noise_bps > 0:
                        p *= 1.0 + rng.normal(0, self.noise_bps / 10000.0)
                    ts_list.append(int(pd.Timestamp(base + timedelta(seconds=k * step)).value))
                    px_list.append(p)
                    vol_list.append(vol / max(1, n_ticks * 2))

        ts_arr = np.asarray(ts_list, dtype=np.int64)
        px_arr = np.asarray(px_list, dtype=np.float64)
        vol_arr = np.asarray(vol_list, dtype=np.float64)
        if len(ts_arr) == 0:
            empty_f = np.array([], dtype=np.float64)
            return TickFrame(symbol, ts_arr, empty_f, empty_f, empty_f, empty_f, empty_f)

        order = np.argsort(ts_arr, kind="stable")
        ts_arr = ts_arr[order]
        px_arr = px_arr[order]
        vol_arr = vol_arr[order]
        # bid/ask 用 ±1bp 近似（模拟数据没有真实盘口）
        bid = px_arr * (1.0 - 0.0001)
        ask = px_arr * (1.0 + 0.0001)
        amount = px_arr * vol_arr
        return TickFrame(symbol, ts_arr, px_arr, bid, ask, vol_arr, amount)


def build_tick_frame_from_provider(
    provider: TickProvider, symbol: str, start: date, end: date
) -> TickFrame:
    """从 provider 取 tick 并做基本健全性检查。"""
    tf = provider.fetch(symbol, start, end)
    if tf.size == 0:
        return tf
    # 时间戳必须严格非递减（C++ 侧用二分查找依赖有序性）
    if np.any(np.diff(tf.ts) < 0):
        order = np.argsort(tf.ts, kind="stable")
        tf = TickFrame(symbol, tf.ts[order], tf.price[order], tf.bid[order],
                       tf.ask[order], tf.volume[order], tf.amount[order])
        log.warning("%s 的 tick 时间戳非有序，已重新排序", symbol)
    # 价格必须为正
    bad = tf.price <= 0
    if np.any(bad):
        log.warning("%s 有 %d 个非正价格 tick，已过滤", symbol, int(bad.sum()))
        keep = ~bad
        tf = TickFrame(symbol, tf.ts[keep], tf.price[keep], tf.bid[keep],
                       tf.ask[keep], tf.volume[keep], tf.amount[keep])
    return tf


__all__ = [
    "TickFrame",
    "TickProvider",
    "SyntheticTickProvider",
    "build_tick_frame_from_provider",
    "NS_PER_SEC",
]
