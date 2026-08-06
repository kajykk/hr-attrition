"""ORM 模型聚合导入，便于 Alembic autogenerate 与 app.main 引用."""
from app.models.audit_log import AuditLog
from app.models.department import Department
from app.models.employee import Employee
from app.models.risk_prediction import RiskPrediction
from app.models.tenant import Tenant
from app.models.user import User
from app.models.warning import WarningEvent, WarningRecord

__all__ = [
    "AuditLog",
    "Department",
    "Employee",
    "RiskPrediction",
    "Tenant",
    "User",
    "WarningEvent",
    "WarningRecord",
]
