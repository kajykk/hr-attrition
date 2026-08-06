"""员工 schemas（参考 D05 3.2）."""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.core.timeutil import today


class EmployeeCreate(BaseModel):
    """新增员工请求体 - 明文 PII 由服务层加密后入库.

    P2-9 校验补齐：
      - email 格式（EmailStr）
      - birth_date 不得晚于 hire_date（若两者均提供）
      - salary_percentile 已有 ge/le 约束
    """

    employee_no: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100, description="姓名（明文，服务层 Fernet 加密）")
    id_card: str | None = Field(default=None, description="身份证号（明文）")
    phone: str | None = Field(default=None, description="手机号（明文）")
    email: EmailStr | None = None
    gender: str | None = Field(default=None, max_length=10, description="仅公平性审计，模型禁用")
    ethnicity: str | None = Field(default=None, description="民族（明文，V1.1 新增，单独同意）")
    disability: str | None = Field(default=None, description="残疾状况（明文，V1.1 新增，单独同意）")
    birth_date: date | None = None
    department_id: UUID | None = None
    position: str | None = Field(default=None, max_length=100)
    level: str | None = Field(default=None, max_length=20)
    hire_date: date
    salary: str | None = Field(default=None, description="薪资绝对值（明文）")
    salary_percentile: Decimal | None = Field(default=None, ge=0, le=100)
    # ===== P0-4 真实特征字段（可空，缺失时特征层回退训练分布占位） =====
    distance_from_home: int | None = Field(default=None, ge=0, le=200)
    education: int | None = Field(default=None, ge=1, le=5)
    environment_satisfaction: int | None = Field(default=None, ge=1, le=4)
    job_involvement: int | None = Field(default=None, ge=1, le=4)
    job_level: int | None = Field(default=None, ge=1, le=5)
    job_satisfaction: int | None = Field(default=None, ge=1, le=4)
    num_companies_worked: int | None = Field(default=None, ge=0, le=50)
    percent_salary_hike: int | None = Field(default=None, ge=0, le=100)
    performance_rating: int | None = Field(default=None, ge=1, le=4)
    relationship_satisfaction: int | None = Field(default=None, ge=1, le=4)
    stock_option_level: int | None = Field(default=None, ge=0, le=3)
    total_working_years: int | None = Field(default=None, ge=0, le=60)
    training_times_last_year: int | None = Field(default=None, ge=0, le=20)
    work_life_balance: int | None = Field(default=None, ge=1, le=4)
    years_in_current_role: int | None = Field(default=None, ge=0, le=50)
    years_since_last_promotion: int | None = Field(default=None, ge=0, le=50)
    years_with_curr_manager: int | None = Field(default=None, ge=0, le=50)
    overtime: bool | None = None
    business_travel: str | None = Field(
        default=None, max_length=20, description="Non-Travel / Travel_Rarely / Travel_Frequently"
    )
    marital_status: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _check_dates(self):
        if self.birth_date and self.hire_date and self.birth_date > self.hire_date:
            raise ValueError("出生日期不得晚于入职日期")
        return self


class EmployeeListItem(BaseModel):
    """员工列表项（脱敏，参考 D05 3.2 GET /employees）."""

    id: UUID
    employee_no: str
    name_masked: str = Field(description="脱敏姓名（如 '张*'）")
    department_name: str | None = None
    position: str | None = None
    status: str
    risk_score: int | None = None
    risk_level: str | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmployeeDetail(BaseModel):
    """员工详情（按角色脱敏）."""

    id: UUID
    employee_no: str
    name_masked: str
    phone_masked: str | None = None
    id_card_masked: str | None = None
    email: str | None = None
    gender: str | None = None
    department_id: UUID | None = None
    department_name: str | None = None
    position: str | None = None
    level: str | None = None
    hire_date: date
    salary_percentile: Decimal | None = None
    status: str
    leave_date: date | None = None
    leave_reason: str | None = None
    consent_status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmployeeLeaveUpdate(BaseModel):
    """离职标记（D05 3.10 PATCH /employees/{id}/leave，FR-EMP-008）."""

    leave_date: date
    leave_reason: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _check_leave_date(self):
        if self.leave_date > today():
            raise ValueError("离职日期不得晚于今天")
        return self


class PaginatedEmployees(BaseModel):
    items: list[EmployeeListItem]
    total: int
    page: int
    page_size: int
