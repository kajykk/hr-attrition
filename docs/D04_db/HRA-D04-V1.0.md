# HRA-D04 数据库设计文档

| 项 | 值 |
|---|---|
| 文档编号 | HRA-D04-V1.0 |
| 文档版本 | V1.1 |
| 编制日期 | 2026-07-27 |
| 文档状态 | 草稿 |
| 数据库 | PostgreSQL 15 |

---

## 1. 设计原则与命名规范

### 1.1 设计原则

- 多租户行级隔离：所有业务表含 `tenant_id` 字段
- 软删除优先：业务表含 `deleted_at` 字段，物理删除通过定时任务
- 审计字段统一：`created_at` / `updated_at` / `created_by` / `updated_by`
- PII 加密：薪资/身份证号/手机号等字段 Fernet 加密存储
- JSONB 优先：可变结构数据使用 JSONB（如 SHAP 解释、模型元数据）

### 1.2 命名规范

| 对象 | 规范 | 示例 |
|---|---|---|
| 表名 | 蛇形小写 + 复数 | `employees`、`risk_predictions` |
| 字段名 | 蛇形小写 | `tenant_id`、`risk_score` |
| 主键 | `id` (UUID v7) | `01923a5b-...` |
| 外键 | `{引用表单数}_id` | `employee_id`、`tenant_id` |
| 索引 | `idx_{表}_{字段}` | `idx_employees_tenant_id` |
| 唯一索引 | `uk_{表}_{字段}` | `uk_users_email_tenant` |
| 外键约束 | `fk_{表}_{引用表}` | `fk_employees_tenant` |
| 枚举值 | 大写下划线 | `STATUS_NEW`、`LEVEL_HIGH` |

### 1.3 字符集与时区

- 字符集：UTF-8
- 排序规则：`zh_CN.UTF-8`（支持中文排序）
- 时区：UTC 存储，前端按用户时区展示
- 时间类型：`TIMESTAMPTZ`（带时区）

---

## 2. ER 模型

### 2.1 概念模型

```
              ┌────────┐
              │ tenants│
              └───┬────┘
                  │ 1
                  │
                  ▼ N
            ┌──────────┐         ┌──────────────┐
            │  users   │◀────────│   roles      │
            └────┬─────┘  N:N    └──────────────┘
                 │ 1
                 │
                 ▼ N
            ┌──────────┐         ┌──────────────┐
            │employees │────────▶│  departments │
            └────┬─────┘  N:1    └──────────────┘
                 │ 1
                 │
        ┌────────┼─────────┬──────────────┐
        ▼        ▼         ▼              ▼
┌──────────┐┌────────┐┌──────────┐┌──────────┐
│predictions││warnings││ appeals  ││interventions│
└────┬─────┘└───┬────┘└────┬─────┘└─────┬────┘
     │          │          │            │
     ▼          ▼          ▼            ▼
┌──────────┐┌────────┐┌──────────┐┌──────────┐
│shap_expls││warning ││appeal_   ││advise_   │
│          ││events  ││events    ││records   │
└──────────┘└────────┘└──────────┘└──────────┘

横切表:
  audit_logs / model_versions / fairness_reports / consent_records
```

---

## 3. 表结构详细设计

### 3.1 租户与用户

#### tenants（租户表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| name | VARCHAR(100) | 否 | - | 租户名称 |
| code | VARCHAR(50) | 否 | - | 租户编码（唯一） |
| plan | VARCHAR(20) | 否 | 'standard' | 套餐：standard/pro/enterprise |
| status | VARCHAR(20) | 否 | 'active' | 状态：active/suspended/closed |
| encryption_key_id | UUID | 否 | - | Fernet 密钥 ID |
| settings | JSONB | 是 | '{}' | 租户配置（阈值/通知渠道） |
| created_at | TIMESTAMPTZ | 否 | now() | 创建时间 |
| updated_at | TIMESTAMPTZ | 否 | now() | 更新时间 |

索引：`uk_tenants_code`、`idx_tenants_status`

#### users（用户表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| email | VARCHAR(255) | 否 | - | 邮箱（登录账号） |
| password_hash | VARCHAR(255) | 否 | - | bcrypt 哈希 |
| name | VARCHAR(100) | 否 | - | 用户姓名 |
| role | VARCHAR(20) | 否 | 'hrbp' | 角色：admin/hr_manager/hrbp/manager/employee |
| status | VARCHAR(20) | 否 | 'active' | 状态：active/disabled |
| totp_secret | VARCHAR(255) | 是 | - | 2FA 密钥（加密） |
| last_login_at | TIMESTAMPTZ | 是 | - | 最后登录 |
| last_login_ip | INET | 是 | - | 最后登录 IP |
| failed_login_count | INT | 否 | 0 | 失败次数 |
| created_at | TIMESTAMPTZ | 否 | now() | - |
| updated_at | TIMESTAMPTZ | 否 | now() | - |
| deleted_at | TIMESTAMPTZ | 是 | - | 软删除 |

索引：`uk_users_tenant_email`、`idx_users_role`、`idx_users_status`

#### employees（员工表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| employee_no | VARCHAR(50) | 否 | - | 工号（租户内唯一） |
| name_encrypted | TEXT | 否 | - | 姓名（Fernet 加密） |
| name_hash | VARCHAR(64) | 否 | - | 姓名 SHA256（检索用） |
| id_card_encrypted | TEXT | 是 | - | 身份证号（Fernet） |
| phone_encrypted | TEXT | 是 | - | 手机号（Fernet） |
| email | VARCHAR(255) | 是 | - | 邮箱 |
| gender | VARCHAR(10) | 是 | - | 性别（仅用于公平性审计，模型禁用） |
| ethnicity_encrypted | TEXT | 是 | - | 民族（Fernet 加密，仅公平性审计，模型禁用，单独同意） |
| ethnicity_hash | VARCHAR(64) | 是 | - | 民族 SHA256（检索用） |
| disability_encrypted | TEXT | 是 | - | 残疾状况（Fernet 加密，仅公平性审计，模型禁用，单独同意） |
| disability_hash | VARCHAR(64) | 是 | - | 残疾状况 SHA256（检索用） |
| birth_date | DATE | 是 | - | 出生日期（用于年龄公平性审计） |
| department_id | UUID | 是 | - | 部门 ID |
| position | VARCHAR(100) | 是 | - | 岗位 |
| level | VARCHAR(20) | 是 | - | 职级 |
| hire_date | DATE | 否 | - | 入职日期 |
| salary_percentile | DECIMAL(5,2) | 是 | - | 薪资分位（0-100） |
| salary_encrypted | TEXT | 是 | - | 薪资绝对值（Fernet） |
| status | VARCHAR(20) | 否 | 'active' | 在职/离职/试用期 |
| leave_date | DATE | 是 | - | 离职日期 |
| leave_reason | VARCHAR(100) | 是 | - | 离职原因 |
| consent_status | VARCHAR(20) | 否 | 'pending' | 同意状态 |
| consent_at | TIMESTAMPTZ | 是 | - | 同意时间 |
| created_at | TIMESTAMPTZ | 否 | now() | - |
| updated_at | TIMESTAMPTZ | 否 | now() | - |
| deleted_at | TIMESTAMPTZ | 是 | - | 软删除 |

索引：`uk_employees_tenant_no`、`idx_employees_department`、`idx_employees_status`、`idx_employees_name_hash`、`idx_employees_ethnicity_hash`、`idx_employees_disability_hash`

### 3.2 部门与组织

#### departments（部门表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| name | VARCHAR(100) | 否 | - | 部门名称 |
| parent_id | UUID | 是 | - | 上级部门 ID |
| manager_id | UUID | 是 | - | 部门负责人 ID |
| path | LTREE | 否 | - | 层级路径（如 root.eng.backend） |
| headcount | INT | 否 | 0 | 编制人数 |
| created_at | TIMESTAMPTZ | 否 | now() | - |
| updated_at | TIMESTAMPTZ | 否 | now() | - |

索引：`uk_departments_tenant_name`、`idx_departments_parent`、`gist_departments_path`（GiST 索引）

### 3.3 风险预测

#### risk_predictions（风险预测表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| employee_id | UUID | 否 | - | 员工 ID |
| model_version | VARCHAR(50) | 否 | - | 模型版本 |
| risk_score | INT | 否 | - | 风险分 0-100 |
| risk_level | VARCHAR(20) | 否 | - | 风险等级 |
| modality_scores | JSONB | 否 | '{}' | 各模态分数 |
| feature_values | JSONB | 否 | '{}' | 输入特征值 |
| predicted_at | TIMESTAMPTZ | 否 | now() | 预测时间 |
| batch_id | UUID | 是 | - | 批次 ID（批量预测） |
| created_at | TIMESTAMPTZ | 否 | now() | - |

索引：`idx_predictions_tenant_employee_time`、`idx_predictions_batch`、`idx_predictions_model_version`

分区策略：按 `predicted_at` 月度分区（保留 2 年），主键为 `(id, predicted_at)`（PostgreSQL 分区键须属主键）

#### shap_explanations（SHAP 解释表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| prediction_id | UUID | 否 | - | 预测 ID |
| tenant_id | UUID | 否 | - | 租户 ID |
| factors | JSONB | 否 | - | Top3 因子列表 |
| base_value | DECIMAL(10,6) | 否 | - | 基线值 |
| output_value | DECIMAL(10,6) | 否 | - | 输出值 |
| model_version | VARCHAR(50) | 否 | - | 模型版本 |
| computed_at | TIMESTAMPTZ | 否 | now() | 计算时间 |
| expires_at | TIMESTAMPTZ | 否 | - | 缓存过期时间 |

索引：`uk_shap_prediction`、`idx_shap_expires`

### 3.4 预警与干预

#### warnings（预警表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| employee_id | UUID | 否 | - | 员工 ID |
| prediction_id | UUID | 是 | - | 关联预测 ID |
| level | VARCHAR(20) | 否 | - | 等级：P0/P1/P2 |
| risk_score | INT | 否 | - | 触发时风险分 |
| status | VARCHAR(20) | 否 | 'new' | 状态：new/confirmed/fixing/review/closed/appealing |
| assigned_to | UUID | 是 | - | 分配 HRBP ID |
| escalated_to | UUID | 是 | - | 升级到（HR 经理）ID |
| message | TEXT | 是 | - | 预警消息 |
| created_at | TIMESTAMPTZ | 否 | now() | - |
| confirmed_at | TIMESTAMPTZ | 是 | - | 确认时间 |
| closed_at | TIMESTAMPTZ | 是 | - | 关闭时间 |

索引：`idx_warnings_tenant_status`、`idx_warnings_assigned`、`idx_warnings_level_time`

#### warning_events（预警事件表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| warning_id | UUID | 否 | - | 预警 ID |
| tenant_id | UUID | 否 | - | 租户 ID |
| action | VARCHAR(30) | 否 | - | 动作：created/confirmed/escalated/fixing/review/closed/commented |
| from_status | VARCHAR(20) | 是 | - | 原状态 |
| to_status | VARCHAR(20) | 是 | - | 新状态 |
| operator_id | UUID | 否 | - | 操作人 ID |
| comment | TEXT | 是 | - | 备注 |
| created_at | TIMESTAMPTZ | 否 | now() | - |

索引：`idx_events_warning`、`idx_events_tenant_time`

#### appeals（申诉表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| warning_id | UUID | 否 | - | 关联预警 ID |
| appellant_id | UUID | 否 | - | 申诉人 ID |
| reason | VARCHAR(30) | 否 | - | 申诉理由：false_alarm/outdated/inaccurate/misleading |
| description | TEXT | 是 | - | 详细描述 |
| status | VARCHAR(20) | 否 | 'pending' | pending/approved/rejected |
| reviewer_id | UUID | 是 | - | 审核人 ID |
| reviewed_at | TIMESTAMPTZ | 是 | - | 审核时间 |
| review_comment | TEXT | 是 | - | 审核意见 |
| created_at | TIMESTAMPTZ | 否 | now() | - |

索引：`idx_appeals_tenant_status`、`idx_appeals_warning`

#### interventions（干预记录表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| warning_id | UUID | 否 | - | 关联预警 ID |
| employee_id | UUID | 否 | - | 员工 ID |
| type | VARCHAR(30) | 否 | - | 类型：raise/transfer/training/coaching/other |
| description | TEXT | 否 | - | 干预内容 |
| executed_by | UUID | 否 | - | 执行人 ID |
| executed_at | TIMESTAMPTZ | 否 | now() | 执行时间 |
| follow_up_at | TIMESTAMPTZ | 是 | - | 回访时间 |
| follow_up_result | VARCHAR(30) | 是 | - | 回访结果：retained/left/unknown |
| created_at | TIMESTAMPTZ | 否 | now() | - |

索引：`idx_interventions_tenant_employee`、`idx_interventions_warning`

#### advise_records（LLM 建议记录表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| warning_id | UUID | 否 | - | 关联预警 ID |
| prediction_id | UUID | 否 | - | 关联预测 ID |
| prompt_sanitized | TEXT | 否 | - | 脱敏后的 prompt |
| response | TEXT | 否 | - | LLM 响应 |
| model_name | VARCHAR(50) | 否 | - | LLM 模型名 |
| tokens_used | INT | 否 | 0 | token 消耗 |
| latency_ms | INT | 否 | 0 | 延迟 |
| created_at | TIMESTAMPTZ | 否 | now() | - |

索引：`idx_advise_tenant_time`、`idx_advise_warning`

### 3.5 模型治理

#### model_versions（模型版本表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 是 | - | 租户 ID（null 表示全局） |
| model_type | VARCHAR(30) | 否 | - | 类型：structured/text/behavior/fusion |
| version | VARCHAR(50) | 否 | - | 版本号 |
| status | VARCHAR(20) | 否 | 'registered' | registered/canary/active/retired |
| metrics | JSONB | 否 | '{}' | 指标（AUC/F1/Recall） |
| fairness_metrics | JSONB | 否 | '{}' | 公平性指标 |
| training_data_hash | VARCHAR(64) | 否 | - | 训练数据 SHA256 |
| feature_names | JSONB | 否 | '[]' | 特征列表 |
| artifacts_path | TEXT | 否 | - | 模型文件路径 |
| sha256 | VARCHAR(64) | 否 | - | 模型文件哈希 |
| canary_percent | INT | 否 | 0 | 金丝雀流量比例 |
| promoted_at | TIMESTAMPTZ | 是 | - | 全量上线时间 |
| retired_at | TIMESTAMPTZ | 是 | - | 退役时间 |
| created_at | TIMESTAMPTZ | 否 | now() | - |

索引：`uk_models_tenant_type_version`、`idx_models_status`

#### drift_alerts（漂移告警表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| model_version | VARCHAR(50) | 否 | - | 模型版本 |
| modality | VARCHAR(20) | 否 | - | 模态：structured/text/behavior/fusion |
| metric_type | VARCHAR(10) | 否 | - | 指标：PSI/KL |
| metric_value | DECIMAL(10,4) | 否 | - | 数值 |
| threshold | DECIMAL(10,4) | 否 | - | 阈值 |
| severity | VARCHAR(20) | 否 | - | 严重度：warning/critical |
| detected_at | TIMESTAMPTZ | 否 | now() | 检测时间 |
| resolved_at | TIMESTAMPTZ | 是 | - | 处理时间 |
| created_at | TIMESTAMPTZ | 否 | now() | - |

索引：`idx_drift_tenant_model`、`idx_drift_severity_time`

#### fairness_reports（公平性报告表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| model_version | VARCHAR(50) | 否 | - | 模型版本 |
| report_date | DATE | 否 | - | 报告日期 |
| metrics | JSONB | 否 | '{}' | 4 项指标（gender/age/ethnicity/disabled） |
| max_deviation | DECIMAL(5,2) | 否 | 0 | 最大偏差 |
| passed | BOOLEAN | 否 | true | 是否通过 |
| created_at | TIMESTAMPTZ | 否 | now() | - |

索引：`idx_fairness_tenant_date`、`idx_fairness_model`

### 3.6 审计与合规

#### audit_logs（审计日志表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| user_id | UUID | 是 | - | 操作人 ID |
| ip | INET | 是 | - | IP 地址 |
| user_agent | TEXT | 是 | - | UA |
| action | VARCHAR(50) | 否 | - | 动作 |
| resource_type | VARCHAR(30) | 否 | - | 资源类型 |
| resource_id | UUID | 是 | - | 资源 ID |
| before_value | JSONB | 是 | - | 变更前 |
| after_value | JSONB | 是 | - | 变更后 |
| prev_hash | VARCHAR(64) | 否 | - | 上一条哈希 |
| current_hash | VARCHAR(64) | 否 | - | 当前哈希 |
| created_at | TIMESTAMPTZ | 否 | now() | - |

索引：`idx_audit_tenant_time`、`idx_audit_user`、`idx_audit_resource`

分区策略：按 `created_at` 月度分区（保留 5 年），主键为 `(id, created_at)`（PostgreSQL 分区键须属主键）

#### consent_records（同意记录表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| employee_id | UUID | 否 | - | 员工 ID |
| consent_type | VARCHAR(30) | 否 | - | 同意类型：prediction/data_use/export |
| status | VARCHAR(20) | 否 | 'granted' | granted/revoked |
| granted_at | TIMESTAMPTZ | 是 | - | 同意时间 |
| revoked_at | TIMESTAMPTZ | 是 | - | 撤回时间 |
| ip | INET | 是 | - | IP |
| user_agent | TEXT | 是 | - | UA |
| created_at | TIMESTAMPTZ | 否 | now() | - |

索引：`idx_consent_tenant_employee`、`idx_consent_status`

#### data_subject_requests（数据权利请求表）

| 字段 | 类型 | 可空 | 默认 | 含义 |
|---|---|---|---|---|
| id | UUID | 否 | - | 主键 |
| tenant_id | UUID | 否 | - | 租户 ID |
| employee_id | UUID | 否 | - | 员工 ID |
| request_type | VARCHAR(20) | 否 | - | 类型：access/export/delete/rectify |
| status | VARCHAR(20) | 否 | 'pending' | pending/processing/completed/rejected |
| description | TEXT | 是 | - | 描述 |
| processed_by | UUID | 是 | - | 处理人 ID |
| processed_at | TIMESTAMPTZ | 是 | - | 处理时间 |
| result_path | TEXT | 是 | - | 结果文件路径（导出） |
| created_at | TIMESTAMPTZ | 否 | now() | - |
| deadline_at | TIMESTAMPTZ | 否 | - | 处理截止（30 天） |

索引：`idx_dsr_tenant_status`、`idx_dsr_deadline`

---

## 4. 数据字典

### 4.1 风险等级

| 等级 | 数值范围 | 颜色 | 含义 |
|---|---|---|---|
| low | 0-19 | 绿色 | 低风险 |
| medium_low | 20-39 | 蓝色 | 中低风险 |
| medium | 40-59 | 黄色 | 中等风险 |
| medium_high | 60-79 | 橙色 | 中高风险 |
| high | 80-100 | 红色 | 高风险 |

### 4.2 预警等级

| 等级 | 触发条件 | 通知方式 | 升级时间 |
|---|---|---|---|
| P0 | risk_score ≥ 80 | WebSocket + 邮件 + 短信 | 24h |
| P1 | 60 ≤ risk_score < 80 | WebSocket + 邮件 | 48h |
| P2 | risk_score 上升 ≥ 20 | WebSocket | 72h |

### 4.3 预警状态

| 状态 | 含义 | 允许的下一状态 |
|---|---|---|
| new | 新建 | confirmed, closed |
| confirmed | 已确认 | review（P0 必经复核）, fixing（P1/P2 可直转）, appealing, closed |
| fixing | 干预中 | review, closed |
| review | 待复核 | closed, fixing |
| appealing | 申诉中 | confirmed, closed |
| closed | 已关闭 | - |

> **状态转换约束**：P0 高级预警遵循 `confirmed → review → fixing`（FR-LOOP-004 强制 HR 经理复核后才能进入干预）；P1/P2 可由 `confirmed` 直转 `fixing`。转换逻辑在 WarningService 中按 `warning.level` 条件分支实现。

---

## 5. 索引与分区策略

### 5.1 高频查询与索引对应

| 查询场景 | 表 | 索引 |
|---|---|---|
| HR 查询员工列表 | employees | idx_employees_tenant_status |
| 按工号检索 | employees | uk_employees_tenant_no |
| 按姓名检索 | employees | idx_employees_name_hash |
| 预警列表分页 | warnings | idx_warnings_tenant_status + created_at |
| 员工预测历史 | risk_predictions | idx_predictions_tenant_employee_time |
| 审计日志查询 | audit_logs | idx_audit_tenant_time + user_id |

### 5.2 分区策略

| 表 | 分区方式 | 保留期 |
|---|---|---|
| risk_predictions | 月度（predicted_at） | 2 年 |
| audit_logs | 月度（created_at） | 5 年 |
| warning_events | 月度（created_at） | 5 年 |
| appeals | 月度（created_at） | 5 年 |
| advise_records | 月度（created_at） | 2 年 |

### 5.3 大表优化

- `risk_predictions` 2 年后预计 1000 万条/租户
- `audit_logs` 5 年后预计 5000 万条/租户
- 优化方案：按租户分库（>50 租户后启用）

---

## 6. 数据迁移与版本管理

### 6.1 Alembic 迁移规范

- 命名：`{YYYYMMDD}_{HHMM}_{slug}.py`
- 类型：`feature`（新表/字段）、`fix`（修复）、`data`（数据迁移）
- 流程：本地测试 → Staging 验证 → 生产执行
- 在线 DDL：使用 `CREATE INDEX CONCURRENTLY` / `ALTER TABLE ... NOT VALID` + `VALIDATE CONSTRAINT`

### 6.2 种子数据

- 系统初始化脚本：`scripts/init_db.py`
- 演示租户：`demo-tenant`（用于功能演示）
- 模型版本：`baseline-v1`（启发式规则模型）

---

## 7. 数据安全设计

### 7.1 PII 字段加密清单

| 表 | 字段 | 加密方式 | 密钥轮换 |
|---|---|---|---|
| employees | name_encrypted | Fernet | 季度 |
| employees | id_card_encrypted | Fernet | 季度 |
| employees | phone_encrypted | Fernet | 季度 |
| employees | salary_encrypted | Fernet | 季度 |
| employees | ethnicity_encrypted | Fernet | 季度 |
| employees | disability_encrypted | Fernet | 季度 |
| users | totp_secret | Fernet | 季度 |

### 7.2 脱敏规则

| 场景 | 字段 | 规则 |
|---|---|---|
| 列表展示 | 手机号 | 138****5678 |
| 列表展示 | 身份证 | 110***********1234 |
| 报表导出 | 薪资 | 仅展示分位 |
| LLM 调用 | 全部 PII | 替换为"员工A" |
| 日志 | 全部 PII | 移除 |

### 7.3 备份与恢复

- 数据库：每日全量（02:00）+ WAL 持续归档
- Redis：每日 RDB（03:00）+ AOF
- 备份保留：30 天滚动
- 恢复演练：每月 1 次

---

## 8. 容量规划

### 8.1 数据量测算（单租户，1000 员工）

| 表 | 月增量 | 年增量 | 2 年总量 |
|---|---|---|---|
| employees | 100 | 1200 | 2400 |
| risk_predictions | 30000 | 360000 | 720000 |
| warnings | 1000 | 12000 | 24000 |
| audit_logs | 50000 | 600000 | 1200000 |
| shap_explanations | 30000 | 360000 | 720000 |

### 8.2 存储估算

- 单租户 2 年：约 5 GB（含索引）
- 50 租户：250 GB
- 优化：分区 + 归档冷数据至 OSS

---

## 9. 变更记录

| 版本 | 日期 | 变更人 | 变更内容 | 审核人 |
|---|---|---|---|---|
| V1.0 | 2026-07-27 | 邝振华 | 初稿创建 | - |
| V1.1 | 2026-07-27 | 邝振华 | 设计审查修订：employees 新增民族/残疾公平性字段；4.3 预警状态机 P0 条件转换；5.2 appeals 分区；分区表主键含分区键 | - |
