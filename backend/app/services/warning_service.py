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

状态机五项补齐（feat/rag-kb）：
  1. 防重复建警：create_warning 先查同租户同员工未关闭同级预警，
     存在则跳过 / 更高级别合并升级；DB 层部分唯一索引（迁移 0006）兜底并发窗口
  2. 自动升级：见 app/tasks/warning_escalation.py（Celery beat 每 6h）
  3. 并发锁：apply_transition 统一转换入口，SELECT ... FOR UPDATE 行锁 +
     唯一索引冲突 rollback 后 retry 一次
  4. 申诉次数上限：check_appeal_limit（>= MAX_APPEAL_COUNT 抛 AppealLimitExceeded，
     API 层映射 409「申诉次数已达上限」）
  5. 保留首次确认时间：appealing→confirmed 回退仅在 confirmed_at 为空时写入
"""
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.warning import (
    ACTIVE_STATUSES,
    ALLOWED_TRANSITIONS,
    LEVEL_ORDER,
    LEVEL_P0,
    LEVEL_P1,
    LEVEL_P2,
    MAX_APPEAL_COUNT,
    STATUS_CLOSED,
    STATUS_CONFIRMED,
    STATUS_FIXING,
    STATUS_NEW,
    WarningEvent,
    WarningRecord,
)


class AppealLimitExceeded(ValueError):
    """申诉次数已达上限（API 层映射 409）."""


# 系统操作人（管线自动建警/自动升级事件的 operator_id 占位）
SYSTEM_OPERATOR_ID = UUID("00000000-0000-0000-0000-000000000000")


def _level_severity(level: str) -> int:
    """等级严重度数值（越大越严重），未知等级按 P1 处理."""
    return LEVEL_ORDER.index(level) if level in LEVEL_ORDER else LEVEL_ORDER.index(LEVEL_P1)


class WarningService:
    """预警状态机服务 - 复用 DWS states.py 思路，新增 P0 条件分支（V1.1）."""

    # 基础合法转换（与 ALLOWED_TRANSITIONS 一致）
    TRANSITIONS: dict[str, list[str]] = ALLOWED_TRANSITIONS

    # ===== 等级 → 状态限制 =====
    # P0 高级预警：confirmed 后必须先进 review（HR 经理复核），不可直转 fixing
    P0_FORBIDDEN_FROM_CONFIRMED: ClassVar[set[str]] = {STATUS_FIXING}
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
        comment: str | None = None,
    ) -> tuple[str, str]:
        """执行状态转换（in-place 修改 warning.status，返回 from/to 供事件记录）.

        时间戳维护：
          - confirmed_at 仅在为空时写入（保留首次确认时间，申诉驳回回退不覆盖）
          - closed_at 进入终态时写入

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
        now = datetime.now(UTC)

        # 时间戳维护（confirmed_at 仅首次写入，保留首次确认时间）
        if target_status == STATUS_CONFIRMED:
            if warning.confirmed_at is None:
                warning.confirmed_at = now
        elif target_status == STATUS_CLOSED:
            warning.closed_at = now

        return from_status, target_status

    # ===== 并发锁：统一转换入口（SELECT ... FOR UPDATE + 冲突 retry） =====

    @staticmethod
    async def load_for_update(
        db: AsyncSession,
        tenant_id: UUID,
        warning_id: UUID,
    ):
        """行锁加载预警（SELECT ... FOR UPDATE），跨租户/不存在返回 None."""
        stmt = (
            select(WarningRecord)
            .where(
                WarningRecord.id == warning_id,
                WarningRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @classmethod
    async def apply_transition(
        cls,
        db: AsyncSession,
        tenant_id: UUID,
        warning_id: UUID,
        target_status: str,
        operator_id: UUID,
        comment: str | None = None,
    ) -> tuple[WarningRecord, str, str] | None:
        """统一状态转换入口（并发安全）.

        流程：FOR UPDATE 行锁加载 → 状态机校验转换 → flush；
        唯一索引冲突（并发建警窗口）rollback 后整体 retry 一次。

        返回:
            (warning, from_status, to_status)；预警不存在返回 None。
            冲突重试后仍失败则抛出 IntegrityError。
        """
        last_exc: IntegrityError | None = None
        for _attempt in range(2):  # retry 一次
            w = await cls.load_for_update(db, tenant_id, warning_id)
            if w is None:
                return None
            from_status, to_status = cls.transition(w, target_status, operator_id, comment)
            try:
                await db.flush()
                return w, from_status, to_status
            except IntegrityError as exc:
                # 并发冲突（如同时刻另一事务关闭了同 employee 的同级预警释放唯一槽位）
                await db.rollback()
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    # ===== 申诉次数上限 =====

    @staticmethod
    def check_appeal_limit(warning) -> None:
        """校验申诉次数上限（>= MAX_APPEAL_COUNT 抛 AppealLimitExceeded）.

        异常:
            AppealLimitExceeded: 次数达上限（ValueError 子类，API 层映射 409）
        """
        appeal_count = getattr(warning, "appeal_count", 0) or 0
        if appeal_count >= MAX_APPEAL_COUNT:
            raise AppealLimitExceeded(
                f"申诉次数已达上限（已申诉 {appeal_count} 次，上限 {MAX_APPEAL_COUNT} 次）"
            )

    @classmethod
    async def create_warning(
        cls,
        db: AsyncSession,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        level: str,
        risk_score: int,
        prediction_id: UUID | None = None,
        message: str | None = None,
        operator_id: UUID = SYSTEM_OPERATOR_ID,
    ) -> tuple[WarningRecord, bool] | None:
        """风险预测管线建警入口（防重复建警）.

        去重/合并规则：
          - 已存在同员工未关闭同级预警 → 跳过（返回现有记录，created=False）
          - 已存在更低级别未关闭预警且新级别更高 → 升级合并到最高级别
          - 无任何未关闭预警 → 新建（status=new）+ created 事件
        DB 层部分唯一索引（迁移 0006 uq_warnings_active_tenant_emp_level）兜底
        并发窗口：flush 冲突时 rollback 后 retry 一次（重查后通常命中去重分支）。

        返回:
            (warning, created)；created=True 表示新建记录（合并升级返回 False）。
        """
        last_exc: IntegrityError | None = None
        for _attempt in range(2):
            active = await cls._load_active_warnings(db, tenant_id, employee_id)
            same_level = [w for w in active if w.level == level]

            # 1. 同级未关闭预警已存在 → 跳过
            if same_level:
                return same_level[0], False

            # 2. 更高级别合并升级 / 低级别被现有覆盖
            if active:
                highest = max(active, key=lambda w: _level_severity(w.level))
                if _level_severity(level) > _level_severity(highest.level):
                    old_level = highest.level
                    highest.level = level
                    highest.risk_score = max(highest.risk_score or 0, risk_score)
                    highest.prediction_id = prediction_id or highest.prediction_id
                    db.add(cls._build_event(
                        highest, action="escalated",
                        comment=f"防重复建警合并升级：{old_level} → {level}",
                        operator_id=operator_id,
                    ))
                return highest, False

            # 3. 无未关闭预警 → 新建
            warning = WarningRecord(
                tenant_id=tenant_id,
                employee_id=employee_id,
                prediction_id=prediction_id,
                level=level,
                risk_score=risk_score,
                status=STATUS_NEW,
                message=message,
                appeal_count=0,
                created_at=datetime.now(UTC),
            )
            db.add(warning)
            db.add(cls._build_event(
                warning, action="created", to_status=STATUS_NEW,
                comment="风险预测触发建警", operator_id=operator_id,
            ))
            try:
                await db.flush()
                return warning, True
            except IntegrityError as exc:
                # 并发窗口：另一事务刚为同员工同级别建警 → rollback 重查走跳过分支
                await db.rollback()
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    @staticmethod
    async def _load_active_warnings(
        db: AsyncSession,
        tenant_id: UUID,
        employee_id: UUID,
    ) -> list[WarningRecord]:
        """查询同员工全部未关闭预警（防重复建警作用域）."""
        stmt = select(WarningRecord).where(
            WarningRecord.tenant_id == tenant_id,
            WarningRecord.employee_id == employee_id,
            WarningRecord.status.in_(ACTIVE_STATUSES),
        ).with_for_update()
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    def _build_event(
        warning: WarningRecord,
        *,
        action: str,
        from_status: str | None = None,
        to_status: str | None = None,
        operator_id: UUID,
        comment: str | None = None,
    ) -> WarningEvent:
        """构造预警事件（审计追溯）."""
        return WarningEvent(
            tenant_id=warning.tenant_id,
            warning_id=warning.id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            operator_id=operator_id,
            comment=comment,
            created_at=datetime.now(UTC),
        )

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
    def level_from_score(risk_score: int, prev_score: int | None = None) -> str:
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
