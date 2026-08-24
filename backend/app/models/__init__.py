"""ORM 模型聚合导入，便于 Alembic autogenerate 与 app.main 引用."""
import logging

from app.models.audit_log import AuditLog
from app.models.behavior_event import BehaviorEvent
from app.models.department import Department
from app.models.employee import Employee
from app.models.risk_prediction import RiskPrediction
from app.models.tenant import Tenant
from app.models.user import User
from app.models.warning import WarningEvent, WarningRecord

# RAG 知识库表（feat/rag-kb）：未安装 .[rag] 依赖组时不注册，主应用零影响
try:
    from app.models.kb import KBChunk, KBDocument
except ImportError:
    logging.getLogger(__name__).debug("kb models 未启用（缺 pgvector），跳过注册")
    KBChunk = None  # type: ignore[assignment,misc]
    KBDocument = None  # type: ignore[assignment,misc]

__all__ = [
    "AuditLog",
    "BehaviorEvent",
    "Department",
    "Employee",
    "KBChunk",
    "KBDocument",
    "RiskPrediction",
    "Tenant",
    "User",
    "WarningEvent",
    "WarningRecord",
]
