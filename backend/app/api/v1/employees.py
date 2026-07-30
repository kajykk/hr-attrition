"""员工路由（D05 3.2 + 3.10 离职标记）."""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import decrypt_pii, encrypt_pii, pii_hash
from app.core.tenant import get_current_tenant_id
from app.db.session import get_db
from app.models.department import Department
from app.models.employee import Employee
from app.models.user import ROLE_ADMIN, ROLE_HRBP, ROLE_HR_MANAGER, ROLE_MANAGER, User
from app.schemas.employee import (
    EmployeeCreate, EmployeeDetail, EmployeeLeaveUpdate, EmployeeListItem,
    PaginatedEmployees,
)

router = APIRouter()


def _mask_name(name: str) -> str:
    """姓名脱敏（D04 7.2）：张三 → 张*."""
    if not name:
        return ""
    return name[0] + "*" * (len(name) - 1) if len(name) > 1 else name[0]


def _mask_phone(phone: str) -> str:
    """手机号脱敏：138****5678."""
    if not phone or len(phone) < 7:
        return phone or ""
    return phone[:3] + "****" + phone[-4:]


def _mask_id_card(id_card: str) -> str:
    """身份证脱敏：110***********1234."""
    if not id_card or len(id_card) < 8:
        return id_card or ""
    return id_card[:3] + "*" * (len(id_card) - 7) + id_card[-4:]


@router.get("", response_model=PaginatedEmployees)
async def list_employees(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    department_id: UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    keyword: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """员工列表（D05 3.2 GET /employees，按租户隔离 + 脱敏）."""
    tenant_id = get_current_tenant_id()

    stmt = select(Employee, Department.name.label("dept_name")).outerjoin(
        Department, Employee.department_id == Department.id
    ).where(
        Employee.tenant_id == tenant_id,
        Employee.deleted_at.is_(None),
    )
    if department_id:
        stmt = stmt.where(Employee.department_id == department_id)
    if status_filter:
        stmt = stmt.where(Employee.status == status_filter)

    # 总数
    count_stmt = select(func.count()).select_from(Employee).where(
        Employee.tenant_id == tenant_id,
        Employee.deleted_at.is_(None),
    )
    if department_id:
        count_stmt = count_stmt.where(Employee.department_id == department_id)
    if status_filter:
        count_stmt = count_stmt.where(Employee.status == status_filter)
    total = (await db.execute(count_stmt)).scalar_one()

    # 分页
    stmt = stmt.order_by(Employee.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).all()

    items = []
    for emp, dept_name in rows:
        name_plain = decrypt_pii(emp.name_encrypted) or ""
        items.append(
            EmployeeListItem(
                id=emp.id,
                employee_no=emp.employee_no,
                name_masked=_mask_name(name_plain),
                department_name=dept_name,
                position=emp.position,
                status=emp.status,
                risk_score=None,
                risk_level=None,
                updated_at=emp.updated_at,
            )
        )

    return PaginatedEmployees(items=items, total=total, page=page, page_size=page_size)


@router.get("/{employee_id}", response_model=EmployeeDetail)
async def get_employee(
    employee_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """员工详情（D05 3.2 GET /employees/{id}，按角色脱敏）."""
    tenant_id = get_current_tenant_id()
    stmt = select(Employee, Department.name.label("dept_name")).outerjoin(
        Department, Employee.department_id == Department.id
    ).where(
        Employee.id == employee_id,
        Employee.tenant_id == tenant_id,
        Employee.deleted_at.is_(None),
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="员工不存在")
    emp, dept_name = row
    name_plain = decrypt_pii(emp.name_encrypted) or ""
    phone_plain = decrypt_pii(emp.phone_encrypted)
    id_card_plain = decrypt_pii(emp.id_card_encrypted)

    return EmployeeDetail(
        id=emp.id,
        employee_no=emp.employee_no,
        name_masked=_mask_name(name_plain),
        phone_masked=_mask_phone(phone_plain) if phone_plain else None,
        id_card_masked=_mask_id_card(id_card_plain) if id_card_plain else None,
        email=emp.email,
        gender=emp.gender,
        department_id=emp.department_id,
        department_name=dept_name,
        position=emp.position,
        level=emp.level,
        hire_date=emp.hire_date,
        salary_percentile=emp.salary_percentile,
        status=emp.status,
        leave_date=emp.leave_date,
        leave_reason=emp.leave_reason,
        consent_status=emp.consent_status,
        created_at=emp.created_at,
        updated_at=emp.updated_at,
    )


@router.post("", response_model=EmployeeDetail, status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: EmployeeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """新增员工（D05 3.2 POST /employees）.

    PII 字段在服务层 Fernet 加密后入库（ADR-007）：
      - name/id_card/phone/salary/ethnicity/disability → encrypt_pii
      - 配套 hash 字段（name_hash/ethnicity_hash/disability_hash）用于检索
    """
    tenant_id = get_current_tenant_id()

    emp = Employee(
        tenant_id=tenant_id,
        employee_no=payload.employee_no,
        # PII 加密字段
        name_encrypted=encrypt_pii(payload.name) or "",
        name_hash=pii_hash(payload.name) or "",
        id_card_encrypted=encrypt_pii(payload.id_card),
        phone_encrypted=encrypt_pii(payload.phone),
        salary_encrypted=encrypt_pii(payload.salary),
        ethnicity_encrypted=encrypt_pii(payload.ethnicity),
        ethnicity_hash=pii_hash(payload.ethnicity),
        disability_encrypted=encrypt_pii(payload.disability),
        disability_hash=pii_hash(payload.disability),
        # 非加密字段
        email=payload.email,
        gender=payload.gender,
        birth_date=payload.birth_date,
        department_id=payload.department_id,
        position=payload.position,
        level=payload.level,
        hire_date=payload.hire_date,
        salary_percentile=payload.salary_percentile,
        status="active",
        consent_status="pending",
    )
    db.add(emp)
    await db.flush()
    await db.refresh(emp)

    return EmployeeDetail(
        id=emp.id,
        employee_no=emp.employee_no,
        name_masked=_mask_name(payload.name),
        phone_masked=_mask_phone(payload.phone) if payload.phone else None,
        id_card_masked=_mask_id_card(payload.id_card) if payload.id_card else None,
        email=emp.email,
        gender=emp.gender,
        department_id=emp.department_id,
        department_name=None,
        position=emp.position,
        level=emp.level,
        hire_date=emp.hire_date,
        salary_percentile=emp.salary_percentile,
        status=emp.status,
        leave_date=emp.leave_date,
        leave_reason=emp.leave_reason,
        consent_status=emp.consent_status,
        created_at=emp.created_at,
        updated_at=emp.updated_at,
    )


@router.patch("/{employee_id}/leave", response_model=EmployeeDetail)
async def mark_leave(
    employee_id: UUID,
    payload: EmployeeLeaveUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """离职标记（D05 3.10 PATCH /employees/{id}/leave，FR-EMP-008）."""
    tenant_id = get_current_tenant_id()
    stmt = select(Employee).where(
        Employee.id == employee_id,
        Employee.tenant_id == tenant_id,
        Employee.deleted_at.is_(None),
    )
    emp = (await db.execute(stmt)).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="员工不存在")

    emp.status = "left"
    emp.leave_date = payload.leave_date
    emp.leave_reason = payload.leave_reason
    await db.flush()
    await db.refresh(emp)

    name_plain = decrypt_pii(emp.name_encrypted) or ""
    return EmployeeDetail(
        id=emp.id,
        employee_no=emp.employee_no,
        name_masked=_mask_name(name_plain),
        email=emp.email,
        gender=emp.gender,
        department_id=emp.department_id,
        position=emp.position,
        level=emp.level,
        hire_date=emp.hire_date,
        salary_percentile=emp.salary_percentile,
        status=emp.status,
        leave_date=emp.leave_date,
        leave_reason=emp.leave_reason,
        consent_status=emp.consent_status,
        created_at=emp.created_at,
        updated_at=emp.updated_at,
    )
