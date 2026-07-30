# HRA 项目文档体系

> 企业员工离职风险与人才流失预警系统（HR Attrition Risk）
> 全套项目文档（D01-D11）

| 项 | 值 |
|---|---|
| 项目代号 | HRA |
| 文档版本 | V1.1 |
| 编制人 | 邝振华 |
| 编制日期 | 2026-07-27 |
| 文档总数 | 11 类核心 + 4 类辅助 |

---

## 文档清单

### 核心文档（D01-D11）

| 编号 | 文档名称 | 路径 | 状态 |
|---|---|---|---|
| D01 | 项目立项报告 | [D01_charter/HRA-D01-V1.0.md](D01_charter/HRA-D01-V1.0.md) | ✅ V1.1 |
| D02 | 需求规格说明书 | [D02_srs/HRA-D02-V1.0.md](D02_srs/HRA-D02-V1.0.md) | ✅ V1.1 |
| D03 | 系统设计文档 | [D03_sad/HRA-D03-V1.0.md](D03_sad/HRA-D03-V1.0.md) | ✅ V1.1 |
| D04 | 数据库设计文档 | [D04_db/HRA-D04-V1.0.md](D04_db/HRA-D04-V1.0.md) | ✅ V1.1 |
| D05 | API 接口文档 | [D05_api/HRA-D05-V1.0.md](D05_api/HRA-D05-V1.0.md) | ✅ V1.1 |
| D06 | 用户操作手册 | [D06_user_manual/HRA-D06-V1.0.md](D06_user_manual/HRA-D06-V1.0.md) | ✅ V1.0 |
| D07 | 测试计划与测试报告 | [D07_test/HRA-D07-V1.0.md](D07_test/HRA-D07-V1.0.md) | ✅ V1.1 |
| D08 | 项目进度计划 | [D08_schedule/HRA-D08-V1.0.md](D08_schedule/HRA-D08-V1.0.md) | ✅ V1.1 |
| D09 | 风险评估报告 | [D09_risk/HRA-D09-V1.0.md](D09_risk/HRA-D09-V1.0.md) | ✅ V1.1 |
| D10 | 上线部署方案 | [D10_deploy/HRA-D10-V1.0.md](D10_deploy/HRA-D10-V1.0.md) | ✅ V1.1 |
| D11 | 项目验收标准 | [D11_acceptance/HRA-D11-V1.0.md](D11_acceptance/HRA-D11-V1.0.md) | ✅ V1.1 |

### 辅助文档（A01-A04，待生成）

| 编号 | 文档名称 | 计划路径 | 状态 |
|---|---|---|---|
| A01 | 运维手册（Runbook）| templates/A01-runbook.md | 已排期（W7 T-709）|
| A02 | 应急预案（ER）| templates/A02-emergency.md | 已排期（W8 T-810）|
| A03 | 模型治理手册 | templates/A03-model-governance.md | 已排期（W8 T-811）|
| A04 | 变更管理记录 | templates/A04-change-log.md | 已排期（W8 T-812）|

---

## 文档关系

```
立项报告(D01) ──→ 需求规格(D02) ──→ 系统设计(D03) ──┬→ API文档(D05)
                                   │                ├→ 数据库设计(D04)
                                   │                └→ 部署方案(D10)
                                                          │
进度计划(D08) ←── 风险评估(D09) ←─贯穿─→ 测试计划(D07) ─→ 验收标准(D11)
                                                          │
                                          用户手册(D06) ←─┘
```

---

## 阅读建议

### 不同角色的阅读路径

| 角色 | 阅读顺序 |
|---|---|
| 决策层 | D01 → D08 → D09 → D11 |
| PM | D01 → D02 → D08 → D09 |
| 架构师 | D02 → D03 → D04 → D05 → D10 |
| 开发 | D02 → D03 → D04 → D05 → D07 |
| 测试 | D02 → D07 → D11 |
| 运维 | D03 → D10 → A01 → A02 |
| 用户 | D06 |
| 法务/合规 | D01 → D02 → D09 → D11 |

---

## 项目核心信息

| 项 | 值 |
|---|---|
| 项目代号 | HRA |
| 项目名称 | 企业员工离职风险与人才流失预警系统 |
| 项目周期 | 2026-07-27 ~ 2026-09-21（8 周）|
| 项目代号 | HRA |
| 技术栈 | Vue 3 + TypeScript + FastAPI + SQLAlchemy + Celery + Redis + PostgreSQL + Docker |
| 复用基础 | DWS 心理健康风险评估系统（复用率 ≥ 60%）|
| 商业模式 | B2B SaaS（年费 3.6-36 万）|
| 目标客户 | 中型企业（200-5000 人）|
| 合规标准 | PIPL + 欧盟 AI Act 高风险类别 |

---

## 编号规范

- 文档编号：`HRA-D[编号]-V[主版本.次版本]`
- 示例：`HRA-D02-V1.0` 表示 HRA 项目需求规格说明书 V1.0 版本

---

## 版本与变更管理

- 主版本：结构变更 / 重大修订
- 次版本：内容补充
- 修订号：笔误修正
- 每份文档末尾附《变更记录表》

---

## 联系方式

- 项目负责人：邝振华
- 邮箱：1754902912@qq.com
- GitHub：https://github.com/kajykk/hr-attrition
