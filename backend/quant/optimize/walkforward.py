"""Walk-forward 折切分。

参数寻优最大的陷阱是"用同一段历史选参数、又用同一段历史评估" —— 那样拿到
的夏普基本是拟合出来的。Walk-forward 把时间切成连续的「训练段 + 测试段」，
在训练段选参数，在**紧接着的、没参与选择的**测试段验证，然后向前滚动。

    折 1:  [----训练----][测试]
    折 2:       [----训练----][测试]
    折 3:            [----训练----][测试]

关键读数不是"测试段收益有多高"，而是 **WFE（walk-forward efficiency）**：
样本外年化收益 / 样本内年化收益。经验上 WFE < 0.5 就说明参数对历史过度敏感，
这套参数上实盘大概率打折。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

__all__ = ["Fold", "WalkForwardError", "make_folds"]


class WalkForwardError(ValueError):
    """折切分参数非法。"""


@dataclass(frozen=True)
class Fold:
    """一个「训练 + 测试」窗口。日期均为闭区间。"""

    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date

    @property
    def train_days(self) -> int:
        return (self.train_end - self.train_start).days + 1

    @property
    def test_days(self) -> int:
        return (self.test_end - self.test_start).days + 1

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
            "train_days": self.train_days,
            "test_days": self.test_days,
        }


def make_folds(
    start: date,
    end: date,
    *,
    train_days: int,
    test_days: int,
    step_days: int | None = None,
) -> list[Fold]:
    """按滚动窗口切分。

    ``step_days`` 默认等于 ``test_days``（相邻测试段首尾相接，不重叠也不留空）。
    设成小于 ``test_days`` 会得到重叠的测试段 —— 那样各折的样本外结果互相包含，
    汇总收益会被重复计算，所以返回值里不做去重，只在这里挡住：
    一旦 ``step_days < test_days``，测试段会重叠，直接报错。

    日期用自然日：数据里没有的日期（周末、停牌、节假日）由引擎按实际 bar
    跳过，不需要在这里对齐交易日历。
    """
    if end <= start:
        raise WalkForwardError(f"结束日期必须晚于开始日期（{start} ~ {end}）")
    if train_days <= 0 or test_days <= 0:
        raise WalkForwardError("训练段与测试段长度必须为正数")

    step = test_days if step_days is None else step_days
    if step <= 0:
        raise WalkForwardError("滚动步长必须为正数")
    if step < test_days:
        raise WalkForwardError(
            f"滚动步长 {step} 天小于测试段 {test_days} 天：测试段会重叠，"
            "样本外收益将被重复计入。请让步长 >= 测试段长度。"
        )

    total_days = (end - start).days + 1
    if total_days < train_days + test_days:
        raise WalkForwardError(f"区间共 {total_days} 天，不足以放下一个 {train_days}+{test_days} 天的窗口")

    folds: list[Fold] = []
    cursor = start
    while True:
        train_start = cursor
        train_end = train_start + timedelta(days=train_days - 1)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=test_days - 1)
        if test_end > end:
            break
        folds.append(
            Fold(
                index=len(folds) + 1,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        cursor = cursor + timedelta(days=step)

    if not folds:
        raise WalkForwardError("没有生成任何折：请检查区间长度与窗口设置")
    return folds
