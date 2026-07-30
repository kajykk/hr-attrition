"""管理员 schemas - Kill Switch 状态与操作（D03 4.5）."""
from typing import Optional

from pydantic import BaseModel


class KillSwitchStatus(BaseModel):
    """Kill Switch 当前状态."""

    active: bool
    reason: Optional[str] = ""
    activated_at: Optional[str] = ""
    activated_by: Optional[str] = ""

    model_config = {"protected_namespaces": ()}


class KillSwitchAction(BaseModel):
    """Kill Switch 激活请求 body."""

    reason: str
