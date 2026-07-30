"""WarningService - 预警状态机（D04 4.3 + V1.1 修订 + FR-LOOP-004）.

状态转换矩阵（基础，未区分 level）：
  new       → confirmed, closed
  confirmed → review, fixing, appealing, closed
  fixing    → review, closed
  review    → closed, fixing
  appealing → confirmed, closed
  closed    → 终态

按 level 条件分支（V1.1 修订，FR-LOOP-004）：
  - P0：confirmed → review 必经（HR 经理复核后方可干预），不可由 confirmed 直转 fixing
  - P1/P2：confirmed → fixing 可直转（无需复核）

非法转换抛出 ValueError，便于测试与上层 API 捕获返回 422。
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.models.warning import (
    ALLOWED_TRANSITIONS,
    LEVEL_P0, LEVEL_P1, LEVEL_P2,
    STATUS_APPEALING, STATUS_CLOSED, STATUS_CONFIRMED,
    STATUS_FIXING, STATUS_NEW, STATUS_REVIEW,
)


class WarningService:
    """预警状态机服务 - 复用 DWS states.py 思路，新增 P0 条件分支（V1.1）."""

    # 基础合法转换（与 ALLOWED_TRANSITIONS 一致）
    TRANSITIONS: dict[str, list[str]] = ALLOWED_TRANSITIONS

    # ===== 等级 → 状态限制 =====
    # P0 高级预警：confirmed 后必须先进 review（HR 经理复核），不可直转 fixing
    P0_FORBIDDEN_FROM_CONFIRMED = {STATUS_FIXING}
    # P1/P2：confirmed 可直转 fixing（review 非强制）

    @classmethod
    def validate_transition(
        cls,
        current_status: str,
        target_status: str,
        level: str,
    ) -> None:
        """校验状态转换合法性（核心方法）.

        参数:
            current_status: 当前状态
            target_status: 目标状态
            level: 预警等级 P0/P1/P2

        异常:
            ValueError: 非法转换（测试可捕获此异常）
        """
        # 终态不可再转换
        if current_status == STATUS_CLOSED:
            raise ValueError(
                f"非法状态转换：预警已关闭（closed 为终态），无法转换至 {target_status}"
            )

        # 基础合法性校验
        allowed = cls.TRANSITIONS.get(current_status, [])
        if target_status not in allowed:
            raise ValueError(
                f"非法状态转换：{current_status} → {target_status}（合法目标：{allowed}）"
            )

        # P0 条件分支：confirmed → fixing 禁止（必须经 review）
        if (
            level == LEVEL_P0
            and current_status == STATUS_CONFIRMED
            and target_status == STATUS_FIXING
        ):
            raise ValueError(
                "P0 高级预警必须经 HR 经理复核：confirmed → review → fixing（FR-LOOP-004）"
            )

    @classmethod
    def transition(
        cls,
        warning,
        target_status: str,
        operator_id: UUID,
        comment: Optional[str] = None,
    ) -> tuple[str, str]:
        """执行状态转换（in-place 修改 warning.status，返回 from/to 供事件记录）.

        参数:
            warning: WarningRecord ORM 实例（含 status/level 字段）
            target_status: 目标状态
            operator_id: 操作人 ID
            comment: 备注

        返回:
            (from_status, to_status)

        异常:
            ValueError: 非法转换
        """
        from_status = warning.status
        level = warning.level

        # 校验合法性（非法抛 ValueError）
        cls.validate_transition(from_status, target_status, level)

        # 执行转换
        warning.status = target_status
        now = datetime.now(timezone.utc)

        # 时间戳维护
        if target_status == STATUS_CONFIRMED:
            warning.confirmed_at = now
        elif target_status == STATUS_CLOSED:
            warning.closed_at = now

        return from_status, target_status

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        """是否终态."""
        return status == STATUS_CLOSED

    @classmethod
    def allowed_next_statuses(cls, status: str, level: str) -> list[str]:
        """返回当前状态可转换的合法目标列表（按 level 过滤）."""
        base = list(cls.TRANSITIONS.get(status, []))
        if level == LEVEL_P0 and status == STATUS_CONFIRMED:
            # 移除 P0 禁止的 fixing 直转
            base = [s for s in base if s not in cls.P0_FORBIDDEN_FROM_CONFIRMED]
        return base

    @staticmethod
    def level_from_score(risk_score: int, prev_score: Optional[int] = None) -> str:
        """根据风险分判定预警等级（D04 4.2）.

        P0: risk_score >= 80
        P1: 60 <= risk_score < 80
        P2: risk_score 上升 >= 20（趋势预警）
        """
        if risk_score >= 80:
            return LEVEL_P0
        if risk_score >= 60:
            return LEVEL_P1
        if prev_score is not None and (risk_score - prev_score) >= 20:
            return LEVEL_P2
        # 默认 P1（最低预警等级）
        return LEVEL_P1
