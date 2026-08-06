"""P0-4 迁移契约测试 - Employee 模型字段与 alembic 0002 迁移一致性.

覆盖：
  1. Employee ORM 包含全部 20 个真实特征字段（无 DB，仅读 mapper 元数据）
  2. 迁移 0002 upgrade() 为 employees 新增的列 = 模型字段全集
  3. 迁移 0002 downgrade() 覆盖全部新增列（可回滚）
  4. 字段类型契约：数值列 Integer、overtime Boolean、枚举列 String(20)
"""
from pathlib import Path

import pytest
from sqlalchemy import Boolean, Integer, String

from app.models.employee import Employee

# P0-4 真实特征字段契约（与 app/models/employee.py + alembic/versions/0002 对齐）
_INT_FEATURE_COLUMNS = {
    "distance_from_home",
    "education",
    "environment_satisfaction",
    "job_involvement",
    "job_level",
    "job_satisfaction",
    "num_companies_worked",
    "percent_salary_hike",
    "performance_rating",
    "relationship_satisfaction",
    "stock_option_level",
    "total_working_years",
    "training_times_last_year",
    "work_life_balance",
    "years_in_current_role",
    "years_since_last_promotion",
    "years_with_curr_manager",
}
_BOOL_FEATURE_COLUMNS = {"overtime"}
_STR_FEATURE_COLUMNS = {"business_travel", "marital_status"}
FEATURE_COLUMNS = _INT_FEATURE_COLUMNS | _BOOL_FEATURE_COLUMNS | _STR_FEATURE_COLUMNS

_MIGRATION_FILE = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0002_employee_feature_fields.py"


def test_employee_model_has_all_feature_columns():
    """Employee ORM 必须包含全部 P0-4 特征字段."""
    model_cols = set(Employee.__mapper__.columns.keys())
    missing = FEATURE_COLUMNS - model_cols
    assert not missing, f"模型缺少特征字段: {missing}"


def test_employee_model_feature_column_types():
    """数值/布尔/枚举列类型契约（与训练侧特征量纲对应）."""
    mapper = Employee.__mapper__.columns
    for col in _INT_FEATURE_COLUMNS:
        assert isinstance(mapper[col].type, Integer), f"{col} 应为 Integer"
    assert isinstance(mapper["overtime"].type, Boolean), "overtime 应为 Boolean"
    for col in _STR_FEATURE_COLUMNS:
        assert isinstance(mapper[col].type, String), f"{col} 应为 String"
        assert mapper[col].type.length == 20, f"{col} 长度应为 20"


def test_migration_0002_upgrade_adds_exactly_feature_columns():
    """迁移 0002 upgrade() 必须新增且仅新增契约字段."""
    assert _MIGRATION_FILE.exists(), "缺少 alembic/versions/0002_employee_feature_fields.py"
    src = _MIGRATION_FILE.read_text(encoding="utf-8")

    # upgrade 中 add_column("employees", ...) 的列名集合
    added = {
        line.split("sa.Column(")[1].split(",")[0].strip().strip('"')
        for line in src.splitlines()
        if 'op.add_column("employees"' in line
    }
    assert added == FEATURE_COLUMNS, f"迁移新增列与契约不一致: 模型={FEATURE_COLUMNS - added}, 多余={added - FEATURE_COLUMNS}"


def test_migration_0002_downgrade_drops_feature_columns():
    """迁移 0002 downgrade() 必须能回滚全部新增列."""
    import re

    src = _MIGRATION_FILE.read_text(encoding="utf-8")
    drop_block = src.split("def downgrade")[1]
    dropped = set(re.findall(r'"([a-z_]+)"', drop_block))
    assert FEATURE_COLUMNS.issubset(dropped), f"downgrade 未覆盖: {FEATURE_COLUMNS - dropped}"


def test_migration_revision_chain():
    """0002 必须接在 0001 之后（revision/down_revision 契约）."""
    src = _MIGRATION_FILE.read_text(encoding="utf-8")
    assert 'revision = "0002"' in src
    assert 'down_revision = "0001"' in src


@pytest.mark.parametrize("col", sorted(_INT_FEATURE_COLUMNS))
def test_feature_defaults_cover_int_columns(col):
    """feature_provider._FEATURE_DEFAULTS 必须覆盖每个整型特征（缺失回退占位）."""
    from app.ml.feature_provider import _FEATURE_DEFAULTS

    assert col in _FEATURE_DEFAULTS, f"{col} 缺少训练分布占位默认值"
