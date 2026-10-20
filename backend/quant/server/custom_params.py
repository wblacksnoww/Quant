"""兼容层：拼装策略参数注入已移到 :mod:`quant.services.custom_params`。"""

from ..services.custom_params import inject_custom_spec

__all__ = ["inject_custom_spec"]
