"""风控规则对外结构。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["RiskRuleOut"]


class RiskRuleOut(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    severity: str
    params: dict[str, Any] = Field(default_factory=dict)
    trigger_count: int = 0
