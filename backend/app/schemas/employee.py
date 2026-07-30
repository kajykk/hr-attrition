"""员工 schemas（参考 D05 3.2）."""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EmployeeCreate(BaseModel):
    """新增员工请求体 - 明文 PII 由服务层加密后入库."""

    employee_no: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100, description="姓名（明文，服务层 Fernet 加密）")
    id_card: Optional[str] = Field(default=None, description="身份证号（明文）")
    phone: Optional[str] = Field(default=None, description="手机号（明文）")
    email: Optional[str] = None
    gender: Optional[str] = Field(default=None, max_length=10, description="仅公平性审计，模型禁用")
    ethnicity: Optional[str] = Field(default=None, description="民族（明文，V1.1 新增，单独同意）")
    disability: Optional[str] = Field(default=None, description="残疾状况（明文，V1.1 新增，单独同意）")
    birth_date: Optional[date] = None
    department_id: Optional[UUID] = None
    position: Optional[str] = Field(default=None, max_length=100)
    level: Optional[str] = Field(default=None, max_length=20)
    hire_date: date
    salary: Optional[str] = Field(default=None, description="薪资绝对值（明文）")
    salary_percentile: Optional[Decimal] = Field(default=None, ge=0, le=100)


class EmployeeListItem(BaseModel):
    """员工列表项（脱敏，参考 D05 3.2 GET /employees）."""

    id: UUID
    employee_no: str
    name_masked: str = Field(description="脱敏姓名（如 '张*'）")
    department_name: Optional[str] = None
    position: Optional[str] = None
    status: str
    risk_score: Optional[int] = None
    risk_level: Optional[str] = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmployeeDetail(BaseModel):
    """员工详情（按角色脱敏）."""

    id: UUID
    employee_no: str
    name_masked: str
    phone_masked: Optional[str] = None
    id_card_masked: Optional[str] = None
    email: Optional[str] = None
    gender: Optional[str] = None
    department_id: Optional[UUID] = None
    department_name: Optional[str] = None
    position: Optional[str] = None
    level: Optional[str] = None
    hire_date: date
    salary_percentile: Optional[Decimal] = None
    status: str
    leave_date: Optional[date] = None
    leave_reason: Optional[str] = None
    consent_status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmployeeLeaveUpdate(BaseModel):
    """离职标记（D05 3.10 PATCH /employees/{id}/leave，FR-EMP-008）."""

    leave_date: date
    leave_reason: Optional[str] = Field(default=None, max_length=100)


class PaginatedEmployees(BaseModel):
    items: list[EmployeeListItem]
    total: int
    page: int
    page_size: int
