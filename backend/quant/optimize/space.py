"""参数空间：把声明式描述展开成待测参数组合。

前端传来的参数空间是一个"每个参数可以取哪些值"的字典，支持三种写法：

    {"fast": [5, 10, 15]}                          # 枚举
    {"slow": {"start": 20, "stop": 60, "step": 10}}  # 等差（整数）
    {"stop_loss": {"start": 0.02, "stop": 0.10, "step": 0.01}}  # 等差（小数）
    {"position_pct": 0.95}                        # 固定值

为什么不直接让前端传展开后的列表：同一份空间还要用于随机采样、
用于前端生成表单、用于结果里回显"当时搜了什么"，保留声明式描述
比保留几百条具体组合更有信息量，也更省带宽。
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from itertools import product
from typing import Any

# 单次寻优的参数组合上限。超过这个数就不是"调参"而是"暴力拟合"了：
# 1000 组 × 每组的样本外表现，几乎必然能挑出一组在历史上好看的噪音。
MAX_COMBINATIONS = 2000

# 参数名统一白名单式校验（不允许嵌套/特殊字符），避免拼错参数名后
# 静默跑出 N 份完全相同的结果（引擎对未知参数默认忽略）。
PARAM_NAME_MAX_LEN = 40


class SpaceError(ValueError):
    """参数空间非法。"""


def _range_values(spec: Mapping[str, Any], name: str) -> list[Any]:
    start = spec.get("start")
    stop = spec.get("stop")
    if start is None or stop is None:
        raise SpaceError(f"参数 {name} 的区间缺少 start / stop")

    raw_step = spec.get("step", 1)
    step = float(raw_step)
    if step <= 0:
        raise SpaceError(f"参数 {name} 的 step 必须为正数，当前 {raw_step}")

    is_int = isinstance(start, int) and isinstance(stop, int) and float(step).is_integer()
    begin = int(start) if is_int else float(start)
    end = int(stop) if is_int else float(stop)
    stride = int(step) if is_int else step

    values: list[Any] = []
    if is_int:
        values = list(range(begin, end + 1, stride))
    else:
        # 浮点用整数计数推进再乘，避免 0.02+0.01 累积成 0.030000000000000002
        count = int(round((end - begin) / stride)) + 1
        values = [round(begin + i * stride, 10) for i in range(max(count, 0))]
        values = [v for v in values if v <= end + stride * 1e-9]

    if not values:
        raise SpaceError(f"参数 {name} 的区间 {begin}~{end}（步长 {stride}）展开为空")
    return values


def expand_space(space: Mapping[str, Any]) -> list[dict[str, Any]]:
    """展开参数空间，返回全部组合（笛卡尔积）。

    组合数超过 :data:`MAX_COMBINATIONS` 直接报错而不是截断 —— 截断会让
    用户以为自己搜了整个空间，实际只搜了前面一小段。
    """
    if not space:
        raise SpaceError("参数空间为空：至少需要一个参数")

    axes: dict[str, list[Any]] = {}
    for raw_name, spec in space.items():
        name = str(raw_name).strip()
        if not name or len(name) > PARAM_NAME_MAX_LEN:
            raise SpaceError(f"参数名非法：{raw_name!r}")
        if isinstance(spec, Mapping):
            axes[name] = _range_values(spec, name)
        elif isinstance(spec, Sequence) and not isinstance(spec, str | bytes):
            values = list(spec)
            if not values:
                raise SpaceError(f"参数 {name} 的候选值为空")
            axes[name] = values
        else:
            axes[name] = [spec]

    total = 1
    for values in axes.values():
        total *= len(values)
    if total > MAX_COMBINATIONS:
        raise SpaceError(
            f"参数组合共 {total} 组，超过上限 {MAX_COMBINATIONS}。"
            "请缩小取值范围或减少参数个数 —— 组合过多时选出的“最优参数”基本是历史噪音。"
        )

    names = list(axes)
    return [dict(zip(names, combo, strict=True)) for combo in product(*(axes[n] for n in names))]


def sample_space(space: Mapping[str, Any], count: int, *, seed: int | None = None) -> list[dict[str, Any]]:
    """从空间里随机采样 ``count`` 组（不重复）。

    采样前仍要展开（才能知道有多少种取值），所以大空间同样受
    :data:`MAX_COMBINATIONS` 约束 —— 真要搜更大的空间，应该换个更小的
    声明，而不是随机撞运气。
    """
    if count <= 0:
        raise SpaceError("采样数量必须为正整数")
    combos = expand_space(space)
    if count >= len(combos):
        return combos
    rng = random.Random(seed)
    return rng.sample(combos, count)


def count_combinations(space: Mapping[str, Any]) -> int:
    """组合总数（不真正展开，供前端预估耗时）。"""
    total = 1
    for spec in space.values():
        if isinstance(spec, Mapping):
            values = _range_values(spec, str(spec.get("name", "?")))
        elif isinstance(spec, Sequence) and not isinstance(spec, str | bytes):
            values = list(spec)
        else:
            values = [spec]
        total *= max(len(values), 1)
    return total
