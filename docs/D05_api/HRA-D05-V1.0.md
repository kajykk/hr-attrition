# HRA-D05 API 接口文档

| 项 | 值 |
|---|---|
| 文档编号 | HRA-D05-V1.0 |
| 文档版本 | V1.1 |
| 编制日期 | 2026-07-27 |
| 文档状态 | 草稿 |
| API 风格 | REST + WebSocket |
| 基 URL | https://api.hra.example.com/api/v1 |
| OpenAPI Spec | `/api/v1/openapi.json` |

---

## 1. 概述

### 1.1 API 风格

RESTful 风格，资源命名复数形式；长耗时操作（LLM 建议生成）使用 SSE 流式响应；实时推送（预警）使用 WebSocket。

### 1.2 版本策略

- URL 路径版本：`/api/v1/`、`/api/v2/`
- 兼容期：旧版本至少保留 6 个月
- 弃用流程：响应头 `Deprecation: true` + `Sunset: <date>`

---

## 2. 通用约定

### 2.1 认证

| 方式 | 适用场景 | 头部 |
|---|---|---|
| Bearer Token | 用户 API | `Authorization: Bearer <access_token>` |
| API Key | 服务间调用 | `X-API-Key: <key>` |

### 2.2 请求格式

- Content-Type：`application/json; charset=utf-8`
- 字符集：UTF-8
- 时间格式：ISO 8601（`2026-07-27T10:00:00Z`）
- 货币：CNY 分（int）

### 2.3 响应格式

统一响应体：

```json
{
  "code": 0,
  "message": "success",
  "data": { },
  "request_id": "req_01923a5b...",
  "timestamp": "2026-07-27T10:00:00Z"
}
```

### 2.4 错误码体系

| HTTP 码 | 业务码 | 含义 |
|---|---|---|
| 200 | 0 | 成功 |
| 400 | 10001 | 参数校验失败 |
| 401 | 10002 | 未认证 |
| 403 | 10003 | 无权限 |
| 404 | 10004 | 资源不存在 |
| 409 | 10005 | 资源冲突 |
| 422 | 10006 | 业务校验失败 |
| 429 | 10007 | 限流 |
| 500 | 20001 | 服务器内部错误 |
| 502 | 20002 | 上游服务错误（LLM/邮件）|
| 503 | 20003 | 服务不可用（Kill Switch 激活）|

错误响应：

```json
{
  "code": 10001,
  "message": "邮箱格式不正确",
  "errors": [
    {"field": "email", "message": "invalid email format"}
  ],
  "request_id": "req_01923a5b..."
}
```

### 2.5 分页/排序/过滤

- 分页：`?page=1&page_size=20`（max 100）
- 排序：`?sort=-created_at,name`（- 降序）
- 过滤：`?status=active&level=high`
- 时间范围：`?created_at_gte=2026-07-01&created_at_lt=2026-08-01`

### 2.6 幂等性

写操作支持 `Idempotency-Key` 头部，相同 key 24h 内返回首次结果。

### 2.7 限流

| 资源 | 限制 |
|---|---|
| 登录 | 5 次/分钟/IP |
| 普通 API | 100 次/分钟/用户 |
| LLM 建议 | 10 次/分钟/用户 |
| 批量导出 | 5 次/小时/用户 |

超限返回 429 + `Retry-After` 头。

---

## 3. 接口清单

### 3.1 认证模块 AUTH

#### POST /auth/login

用户登录。

请求体：
```json
{
  "email": "user@example.com",
  "password": "P@ssw0rd123!",
  "totp_code": "123456"
}
```

响应（200）：
```json
{
  "code": 0,
  "data": {
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",
    "expires_in": 1800,
    "user": {
      "id": "01923a5b-...",
      "name": "张三",
      "role": "hrbp",
      "tenant_id": "01923a5c-..."
    }
  }
}
```

错误：401（密码错误）/ 423（账号锁定）

#### POST /auth/refresh

刷新令牌。

请求体：`{"refresh_token": "eyJ..."}`

响应（200）：`{"access_token": "...", "expires_in": 1800}`

#### POST /auth/logout

登出（加入 token blocklist）。

#### POST /auth/password/reset

请求密码重置邮件。

请求体：`{"email": "user@example.com"}`

#### POST /auth/password/reset/confirm

确认重置密码。

请求体：`{"token": "...", "new_password": "..."}`

### 3.2 员工模块 EMP

#### POST /employees/batch-import

批量导入员工（CSV/Excel）。

请求：`multipart/form-data`，字段 `file`

响应（200）：
```json
{
  "code": 0,
  "data": {
    "total": 100,
    "success": 95,
    "failed": 5,
    "errors": [
      {"row": 12, "field": "employee_no", "message": "duplicated"}
    ]
  }
}
```

#### GET /employees

员工列表。

参数：`page`、`page_size`、`department_id`、`status`、`keyword`

响应（200）：
```json
{
  "code": 0,
  "data": {
    "items": [
      {
        "id": "...",
        "employee_no": "E001",
        "name_masked": "张*",
        "department_name": "技术部",
        "position": "高级工程师",
        "status": "active",
        "risk_score": 82,
        "risk_level": "high",
        "updated_at": "..."
      }
    ],
    "total": 1000,
    "page": 1,
    "page_size": 20
  }
}
```

#### GET /employees/{id}

员工详情。

响应（200）：完整员工档案（按角色脱敏）。

#### POST /employees

新增员工。

#### PATCH /employees/{id}

更新员工字段（字段级审计）。

#### DELETE /employees/{id}

软删除员工（30 天恢复期）。

#### GET /employees/{id}/risk-history

员工风险历史（时序图）。

响应（200）：
```json
{
  "code": 0,
  "data": [
    {"predicted_at": "2026-07-01", "risk_score": 45, "risk_level": "medium"},
    {"predicted_at": "2026-07-02", "risk_score": 48, "risk_level": "medium"}
  ]
}
```

### 3.3 风险预测模块 RISK

#### POST /risk/predict

单员工风险预测。

请求体：
```json
{
  "employee_id": "01923a5b-...",
  "force_refresh": false
}
```

响应（200）：
```json
{
  "code": 0,
  "data": {
    "prediction_id": "01923a5c-...",
    "employee_id": "...",
    "risk_score": 82,
    "risk_level": "high",
    "modality_scores": {
      "structured": 0.78,
      "text": 0.85,
      "behavior": 0.72
    },
    "model_version": "fusion_v3.2",
    "predicted_at": "2026-07-27T10:00:00Z",
    "cached": false
  }
}
```

#### POST /risk/predict/batch

批量预测（异步任务）。

请求体：`{"department_id": "..."}` 或 `{"employee_ids": ["...", "..."]}`

响应（202）：`{"task_id": "...", "estimated_duration": "300s"}`

#### GET /risk/predict/batch/{task_id}

查询批量任务状态。

#### GET /risk/predict/{prediction_id}/shap

获取 SHAP 解释。

响应（200）：
```json
{
  "code": 0,
  "data": {
    "prediction_id": "...",
    "factors": [
      {
        "feature": "salary_percentile",
        "display_name": "薪资分位",
        "value": 43.5,
        "contribution": -0.15,
        "direction": "negative",
        "description": "薪资分位低于部门中位数 56 个百分点"
      },
      {
        "feature": "promotion_gap_months",
        "display_name": "晋升间隔",
        "value": 28,
        "contribution": 0.12,
        "direction": "positive",
        "description": "距离上次晋升 28 个月"
      }
    ],
    "base_value": 0.35,
    "output_value": 0.82,
    "computed_at": "..."
  }
}
```

#### GET /risk/dashboard

部门风险概览（HR 经理可见）。

参数：`department_id`、`date`

响应（200）：
```json
{
  "code": 0,
  "data": {
    "department": "技术部",
    "headcount": 50,
    "risk_distribution": {
      "low": 30, "medium_low": 12, "medium": 5, "medium_high": 2, "high": 1
    },
    "top_risk_employees": [
      {"employee_id": "...", "name_masked": "李*", "risk_score": 82, "position": "..."}
    ],
    "trend": [
      {"date": "2026-07-01", "avg_score": 32.5},
      {"date": "2026-07-27", "avg_score": 35.2}
    ]
  }
}
```

### 3.4 预警模块 WARN

#### GET /warnings

预警列表（多维度筛选）。

参数：`status`、`level`、`assigned_to`、`department_id`、`created_at_gte`、`created_at_lt`

#### GET /warnings/{id}

预警详情。

#### POST /warnings/{id}/confirm

确认预警。

请求体：`{"comment": "已联系员工"}`

#### POST /warnings/{id}/escalate

升级预警（48h 未确认自动触发）。

#### POST /warnings/{id}/fixing

进入干预中。

请求体：`{"intervention_type": "raise", "description": "调薪 15%"}`

#### POST /warnings/{id}/review

提交复核。

#### POST /warnings/{id}/close

关闭预警。

请求体：`{"result": "retained", "comment": "员工接受调薪"}`

#### POST /warnings/{id}/appeal

申诉预测结果。

请求体：
```json
{
  "reason": "false_alarm",
  "description": "员工刚签署长期合同，无离职意向"
}
```

#### POST /warnings/{id}/mark

标记预测准确性（人在回路）。

请求体：`{"accuracy": "partial", "comment": "..."}`

### 3.5 LLM 建议模块 ADVISE

#### POST /advise/generate（SSE）

生成 LLM 保留建议（流式响应）。

请求体：
```json
{
  "warning_id": "...",
  "prediction_id": "...",
  "advice_types": ["raise", "transfer", "training"]
}
```

响应（200，Content-Type: text/event-stream）：
```
data: {"chunk": "基于员工A的风险归因分析，"}

data: {"chunk": "建议采取以下三项措施：\n\n"}

data: {"chunk": "1. **调薪建议**：员工当前薪资分位 43%，"}

data: {"chunk": "低于部门中位数。建议调薪 15%-20%，"}

data: {"chunk": "使分位提升至 60% 以上。\n\n"}

data: {"metadata": {"tokens_used": 350, "model": "qwen-max", "latency_ms": 4200}}

data: [DONE]
```

#### GET /advise/records

LLM 建议历史。

#### GET /advise/records/{id}

LLM 建议详情（含完整 prompt 与响应）。

### 3.6 模型治理模块 MODEL

#### GET /models

模型版本列表。

#### POST /models

注册新模型。

请求体：
```json
{
  "model_type": "fusion",
  "version": "v3.3",
  "metrics": {"auc": 0.87, "f1": 0.81},
  "fairness_metrics": {"gender_dev": 0.03, "age_dev": 0.04},
  "training_data_hash": "...",
  "feature_names": [...],
  "artifacts_path": "s3://..."
}
```

#### POST /models/{id}/canary

启动金丝雀发布。

请求体：`{"percent": 5, "duration_hours": 72}`

#### GET /models/{id}/canary/status

查询金丝雀状态。

响应：
```json
{
  "code": 0,
  "data": {
    "model_id": "...",
    "stage": "5%",
    "started_at": "...",
    "metrics": {"auc": 0.86, "error_rate": 0.02},
    "passed": true,
    "rollback_triggered": false
  }
}
```

#### POST /models/{id}/canary/promote

升级金丝雀（5% → 25% → 100%）。

#### POST /models/{id}/canary/rollback

手动回滚。

#### GET /models/drift

漂移告警列表。

#### GET /models/fairness/reports

公平性报告列表。

#### POST /models/kill-switch/activate

激活 Kill Switch（管理员）。

请求体：`{"reason": "公平性偏差超阈值"}`

#### POST /models/kill-switch/deactivate

关闭 Kill Switch。

### 3.7 报表模块 REPORT

#### GET /reports/department

部门风险画像报表。

#### GET /reports/trend

趋势分析报表。

#### GET /reports/intervention-effect

干预效果报表。

参数：`start_date`、`end_date`

#### POST /reports/export

导出报表。

请求体：`{"report_type": "department", "format": "pdf", "department_id": "..."}`

响应（202）：`{"task_id": "..."}`

#### GET /reports/export/{task_id}

查询导出任务状态 + 下载链接。

### 3.8 审计与合规模块 AUDIT

#### GET /audit/logs

审计日志查询（管理员）。

#### GET /audit/pii-access

PII 访问日志查询。

#### POST /consent

员工同意签署。

请求体：`{"employee_id": "...", "consent_type": "prediction", "status": "granted"}`

#### POST /consent/revoke

撤回同意。

#### POST /dsr

数据权利请求。

请求体：
```json
{
  "employee_id": "...",
  "request_type": "export",
  "description": "申请导出我的全部数据"
}
```

#### GET /dsr/{id}

查询请求处理状态。

### 3.9 系统管理模块 ADMIN

#### GET /admin/tenants

租户列表（仅超管）。

#### POST /admin/tenants

创建租户。

#### GET /admin/users

用户列表。

#### POST /admin/users

邀请用户。

#### GET /admin/settings

系统设置。

#### PATCH /admin/settings

更新设置（阈值/通知渠道）。

#### GET /admin/health

健康检查（公开）。

响应（200）：
```json
{
  "status": "healthy",
  "components": {
    "database": "healthy",
    "redis": "healthy",
    "celery": "healthy",
    "llm": "healthy"
  }
}
```

---

### 3.10 补充端点（V1.1 增补）

以下端点为 V1.1 设计审查后增补，对应 SRS 中此前未明确端点的需求：

| 端点 | 方法 | 说明 | 对应需求 | 优先级 |
|---|---|---|---|---|
| `/consent/{employee_id}` | GET | 查询员工同意状态（含各 PII 字段单独同意）| FR-AUDIT-006 | P0 |
| `/employees/{id}/leave` | PATCH | 离职标记（`leave_date` + `leave_reason`；`destination_industry` 字段 P2 延后）| FR-EMP-008 | P0 |
| `/risk/global-explanation` | GET | 全局特征重要性（近 30 天聚合）| FR-EXPLAIN-003 | P1 |
| `/risk/counterfactual` | POST | 反事实模拟（调整因子 → 预测变化）| FR-EXPLAIN-004 | P1 |
| `/advise/cases` | POST | 案例库更新（HR 反馈有效建议入库）| FR-ADVISE-005 | P2 |
| `/dsr/{employee_id}/status` | GET | 员工自助门户 DSR 状态查询 | D06 2.5 | P1 |

> 员工自助门户（D06 2.5）完整端点集（访问权/更正权/删除权/可携带权）作为 P1 在 Beta 前补全，复用 AUDIT 模块 DSR 端点。

## 4. WebSocket 事件定义

### 4.1 连接

- URL：`wss://api.hra.example.com/ws?token=<access_token>`
- 心跳：客户端 30s 发送 `{"type": "ping"}`，服务端响应 `{"type": "pong"}`
- 重连：指数退避（1s/2s/4s/8s/16s/30s）

### 4.2 事件类型

#### warning.created（新预警）

```json
{
  "type": "warning.created",
  "data": {
    "warning_id": "...",
    "employee_id": "...",
    "employee_name_masked": "张*",
    "level": "P0",
    "risk_score": 85,
    "department_name": "技术部",
    "created_at": "..."
  }
}
```

#### warning.updated（预警状态变更）

```json
{
  "type": "warning.updated",
  "data": {
    "warning_id": "...",
    "from_status": "new",
    "to_status": "confirmed",
    "operator": "李四",
    "updated_at": "..."
  }
}
```

#### warning.escalated（预警升级）

```json
{
  "type": "warning.escalated",
  "data": {
    "warning_id": "...",
    "from_level": "P1",
    "to_level": "P0",
    "reason": "HR 经理 24h 未确认",
    "escalated_at": "..."
  }
}
```

#### risk.updated（风险分更新）

```json
{
  "type": "risk.updated",
  "data": {
    "employee_id": "...",
    "employee_name_masked": "张*",
    "prev_score": 72,
    "curr_score": 85,
    "risk_level": "high",
    "model_version": "v1.2.0",
    "updated_at": "..."
  }
}
```

#### model.drift（漂移告警）

```json
{
  "type": "model.drift",
  "data": {
    "model_version": "v1.2.0",
    "metric": "PSI",
    "value": 0.28,
    "threshold": 0.2,
    "feature": "salary_percentile",
    "detected_at": "..."
  }
}
```

#### kill_switch.activated（Kill Switch 激活）

```json
{
  "type": "kill_switch.activated",
  "data": {
    "reason": "公平性偏差超阈值",
    "activated_by": "admin",
    "activated_at": "..."
  }
}
```

### 4.3 订阅规则

- 用户自动订阅本租户事件
- 跨租户事件不推送
- HRBP 仅接收分配给自己的预警
- HR 经理接收本部门全部预警
- 管理员接收系统级事件（漂移/Kill Switch）

---

## 5. SDK 与 Mock

### 5.1 SDK

| 语言 | 包名 | 状态 |
|---|---|---|
| Python | `hra-sdk-python` | 计划中 |
| JavaScript | `hra-sdk-js` | 计划中 |
| Java | `hra-sdk-java` | 暂不提供 |

### 5.2 Mock 服务

- URL：`https://mock.hra.example.com`
- 基于 OpenAPI Spec 自动生成
- 支持 5 种场景：成功/参数错误/未授权/限流/服务不可用

---

## 6. 变更管理

### 6.1 兼容性策略

- 新增字段：可选，向后兼容
- 删除字段：6 个月弃用期
- 修改字段类型：新版本路径
- 状态枚举值：仅追加，不删除

### 6.2 变更流程

1. PR 修改 OpenAPI Spec
2. 契约测试自动验证
3. 文档同步更新
4. SemVer 版本号管理

---

## 7. 附录：完整 OpenAPI Spec

完整 OpenAPI 3.1 Spec 文件位于：`backend/app/api/v1/openapi.json`，访问 `/api/v1/openapi.json` 获取最新版本。

Swagger UI：`/docs`
ReDoc：`/redoc`

---

## 8. 变更记录

| 版本 | 日期 | 变更人 | 变更内容 | 审核人 |
|---|---|---|---|---|
| V1.0 | 2026-07-27 | 邝振华 | 初稿创建 | - |
| V1.1 | 2026-07-27 | 邝振华 | 设计审查修订：LLM 模型名统一 qwen-max；4.2 补全 WS 事件 schema；3.10 增补 6 个缺失端点 | - |
