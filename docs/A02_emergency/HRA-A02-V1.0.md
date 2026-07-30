# HRA-A02 应急预案（Emergency Response Plan）

| 项 | 值 |
|---|---|
| 文档编号 | HRA-A02-V1.0 |
| 文档版本 | V1.0 |
| 编制日期 | 2026-07-27 |
| 编制人 | 邝振华 |
| 文档状态 | 正式 |
| 适用范围 | HRA V1.0 生产环境突发事件应急响应 |
| 关联文档 | A01 运维手册 / A03 模型治理手册 / D10 部署方案 |

> 本预案覆盖 HRA 系统 7 类典型应急场景，明确应急组织、响应级别、处置流程与演练计划。所有 P0/P1 事件必须在 30 分钟内启动响应。

---

## 1. 应急组织

### 1.1 应急组织架构

```
┌─────────────────────────────────────┐
│       应急指挥（项目负责人）          │
│            邝振华                    │
└──────────────┬──────────────────────┘
               │
   ┌───────────┼───────────┬───────────┐
   ▼           ▼           ▼           ▼
┌──────┐  ┌──────┐   ┌────────┐  ┌────────┐
│ 运维 │  │ 开发 │   │法律顾问│  │伦理委员会│
│      │  │      │   │        │  │        │
└──────┘  └──────┘   └────────┘  └────────┘
```

### 1.2 角色与职责

| 角色 | 姓名 | 职责 | 决策权限 |
|---|---|---|---|
| 应急指挥 | 邝振华 | 总体协调 / 资源调度 / 对外沟通 / 启动/解除应急 | 全权决策 |
| 运维 | ____ | 现场处置 / 容器管理 / 监控告警 / 日志取证 | 容器重启 / 扩缩容 |
| 开发 | 邝振华 | 代码修复 / 模型回滚 / 数据修复 | 代码 hotfix |
| 法律顾问 | ____ | PIPL 合规评估 / 网信办报告 / 当事人通知 | 合规决策 |
| 伦理委员会 | ____ | 公平性事件审查 / Kill Switch 复核 | 公平性决策 |

### 1.3 值班机制

- **工作日**：09:00-21:00 在线值班（飞书群 @oncall）
- **非工作日**：手机待命，P0 事件 30 分钟内响应
- **值班轮换**：每周轮换，见 `ops/oncall_schedule.md`

---

## 2. 响应级别

### 2.1 级别定义

| 级别 | 定义 | 响应时效 | 解决时效 | 升级条件 | 示例 |
|---|---|---|---|---|---|
| **P0** | 系统不可用 / 数据泄露 | 30min | 4h | 立即通知应急指挥 | 全站宕机 / PII 泄露 / 数据库故障 |
| **P1** | 核心功能降级 | 2h | 24h | 通知项目负责人 | 模型漂移 / LLM 不可用 / 公平性超标 |
| **P2** | 次要功能异常 | 8h | 5 工作日 | 工单记录 | 报表导出失败 / 个别页面 500 |

### 2.2 响应流程

```
事件发现 → 分级判定（P0/P1/P2）→ 启动响应 → 现场处置 → 验证恢复 → 复盘归档
   │                                              │
   └─→ 通知应急指挥（P0/P1）                       └─→ 必要时升级（P2→P1→P0）
```

### 2.3 通知方式

| 级别 | 通知方式 | 抄送 |
|---|---|---|
| P0 | 电话 + 飞书加急 + 短信 | 全体应急组织 |
| P1 | 飞书加急 | 应急指挥 + 责任人 |
| P2 | 飞书工单 | 责任人 |

---

## 3. 应急场景与处置

### 3.1 场景 1：模型漂移导致预测失准

**触发条件**：漂移检测报告 `max_psi >= 0.2`（critical），或连续 3 天 warning。

**影响级别**：P1（核心功能降级）

**处置流程**：

| 步骤 | 操作 | 责任人 | 时效 |
|---|---|---|---|
| 1 | 确认漂移：`cat backend/app/ml/models/drift_report.json` 查看 critical_features | 运维 | 10min |
| 2 | 激活 Kill Switch：`POST /api/v1/admin/kill-switch/activate` body `{"reason":"模型漂移 PSI=%.2f"}` | 运维 | 5min |
| 3 | 通知 HR：通过飞书群告知"模型预测服务降级，预计 Xh 恢复" | 应急指挥 | 15min |
| 4 | 回滚模型：`app/tasks/model_governance::rollback_model()` 切换至上一版本 | 开发 | 30min |
| 5 | 分析漂移根因：特征分布变化 / 数据源异常 / 上游系统变更 | 开发 | 2h |
| 6 | 重新训练：`python -m app.ml.train_pipeline` + 公平性测试 | 开发 | 4h |
| 7 | 金丝雀发布新模型（5%→25%→100%，见 A03）| 开发 | 2h |
| 8 | 解除 Kill Switch：`POST /api/v1/admin/kill-switch/deactivate` | 应急指挥 | 验证后 |

**回滚验证**：
```bash
# 确认模型版本已切换
curl -s http://localhost:8000/api/v1/admin/model-version | jq .version
# 确认 AUC 恢复
cat backend/app/ml/models/fusion_metrics.json | jq .auc_test
```

### 3.2 场景 2：公平性偏差超标

**触发条件**：公平性日报 `max_parity_difference > 0.08`（Kill Switch 自动激活）或 5%-8% 告警。

**影响级别**：P1（伦理风险）/ P0（若已对外发布歧视性预测）

**处置流程**：

| 步骤 | 操作 | 责任人 | 时效 |
|---|---|---|---|
| 1 | 确认 Kill Switch 已自动激活：`GET /api/v1/admin/kill-switch` | 运维 | 5min |
| 2 | 定位超标维度：查看 `fairness_report.json` 的 dimensions | 开发 | 15min |
| 3 | 通知伦理委员会 + 应急指挥 | 运维 | 15min |
| 4 | 排查根因：训练数据偏置 / 特征工程泄漏禁用特征 / 阈值不当 | 开发 | 2h |
| 5 | 公平性重训：`python -m app.ml.fairness_test`（自动阈值调整缓解）| 开发 | 4h |
| 6 | 伦理委员会审查：提交重训报告 + 4 维度指标 | 伦理委员会 | 24h |
| 7 | 审批通过后金丝雀发布 | 开发 | 2h |
| 8 | 解除 Kill Switch（伦理委员会签字后）| 应急指挥 | 审批后 |

**关键约束**：解除 Kill Switch 必须伦理委员会签字（见 A01 Runbook 5 §5.3）。

### 3.3 场景 3：数据库故障

**触发条件**：`/health` 返回 `database: unhealthy`，或 API 报连接超时。

**影响级别**：P0（系统不可用）

**处置流程**：

| 步骤 | 操作 | 责任人 | 时效 |
|---|---|---|---|
| 1 | 确认故障范围：主库宕机 / 连接池耗尽 / 磁盘满 | 运维 | 5min |
| 2 | 激活 Kill Switch（防止缓存不一致）：`POST /api/v1/admin/kill-switch/activate` | 运维 | 5min |
| 3 | 主从切换（RDS 控制台 → 故障切换，<30s） | DBA | 5min |
| 4 | 更新连接串（如主库 IP 变化）：修改 `.env` 的 `DATABASE_URL` | 运维 | 5min |
| 5 | 重启 api/worker：`docker compose up -d api worker` | 运维 | 2min |
| 6 | 数据校验：`SELECT count(*) FROM employees; SELECT count(*) FROM audit_logs;` | DBA | 15min |
| 7 | 解除 Kill Switch | 应急指挥 | 验证后 |

**若是磁盘满**：
```bash
# 紧急清理
docker compose exec postgres psql -U hra -c "VACUUM FULL;"
# 扩容 RDS 存储（控制台在线扩容）
# 归档旧分区（见 A01 Runbook 2 §2.6）
```

**若是连接池耗尽**：
```bash
# 查看长事务
psql -c "SELECT pid, state, query, query_start FROM pg_stat_activity WHERE state!='idle' ORDER BY query_start;"
# 终止长事务
psql -c "SELECT pg_terminate_backend(<pid>);"
# 扩大连接池（临时）：修改 .env 的 DB_POOL_SIZE
```

### 3.4 场景 4：Redis 故障

**触发条件**：`/health` 返回 `redis: unhealthy`，或 Celery 任务不执行。

**影响级别**：P1（核心功能降级，Kill Switch fail-open 不阻塞）

**处置流程**：

| 步骤 | 操作 | 责任人 | 时效 |
|---|---|---|---|
| 1 | 确认 Redis 容器状态：`docker compose ps redis` | 运维 | 5min |
| 2 | 降级无缓存模式：系统自动 fail-open（`app/core/redis.py` 返回 None）| 自动 | 即时 |
| 3 | 重启 Redis：`docker compose restart redis` | 运维 | 2min |
| 4 | 若主从：切换至从库（阿里云 Redis 控制台） | DBA | 5min |
| 5 | 缓存预热：触发批量预测任务填充 `risk:cache:*` | 开发 | 30min |
| 6 | 验证 Celery 任务恢复：`docker compose exec redis redis-cli LLEN celery` | 运维 | 5min |

**注意**：Redis 故障期间，Kill Switch 读取返回 False（fail-open），预测走无缓存慢路径（P99 < 5s，P-PERF-02）。

### 3.5 场景 5：LLM 服务不可用

**触发条件**：DashScope API 连续失败，或 `DASHSCOPE_API_KEY` 失效。

**影响级别**：P1（LLM 建议降级）/ P2（仅展示受影响）

**处置流程**：

| 步骤 | 操作 | 责任人 | 时效 |
|---|---|---|---|
| 1 | 确认降级生效：`docker compose logs api \| grep "rule-template"` | 运维 | 5min |
| 2 | 排查 DashScope 状态：阿里云控制台 → 健康检查 | 运维 | 10min |
| 3 | 若限流（429）：降低 `LLM_RATE_LIMIT`，启用排队 | 开发 | 15min |
| 4 | 若 key 失效：轮换 `DASHSCOPE_API_KEY`（见 A01 Runbook 4 §4.5）| 运维 | 30min |
| 5 | 切备用模型：修改 `.env` 的 `LLM_PRIMARY=deepseek-v3`，重启 api | 运维 | 5min |
| 6 | 验证 SSE 恢复：`curl -X POST /api/v1/advise/stream ...` | 运维 | 5min |

**降级说明**：LLM 全部不可用时，系统走规则模板（`_fallback_template`），按 SHAP 因子生成调薪/晋升/辅导建议，业务不中断。

### 3.6 场景 6：PII 数据泄露

**触发条件**：异常审计日志（`action LIKE 'pii.%'` 高频）/ 外部举报 / 监控告警。

**影响级别**：P0（合规风险）

**处置流程**：详见 A01 Runbook 6（数据泄露响应），核心步骤：

| 阶段 | 时效 | 关键动作 |
|---|---|---|
| 隔离 | 0-1h | Kill Switch 激活 + 切外网 + 冻结账号 |
| 取证 | 1-4h | 审计日志导出 + 哈希链校验 + 容器快照 |
| 内部报告 | 4h | 通知应急指挥 + 法律顾问 |
| 网信办报告 | 72h | PIPL 第 57 条合规报告 |
| 当事人通知 | 72h | 邮件/短信告知受影响员工 |
| 补救 | 7d | 修复根因 + 轮换密钥 + 渗透复测 |
| 复盘 | 14d | 事故报告 + 流程改进 |

### 3.7 场景 7：DDoS 攻击

**触发条件**：API 请求量突增 10x+，或 `RATE_LIMIT_API` 频繁触发。

**影响级别**：P0（系统不可用）/ P1（性能降级）

**处置流程**：

| 步骤 | 操作 | 责任人 | 时效 |
|---|---|---|---|
| 1 | 确认攻击：Grafana 看 QPS 突增 + Nginx access log 异常 IP | 运维 | 5min |
| 2 | 启动限流（slowapi 已内置 `RATE_LIMIT_API=100/minute`）| 自动 | 即时 |
| 3 | 紧急收紧限流：修改 `.env` 的 `RATE_LIMIT_API=20/minute`，重启 api | 运维 | 5min |
| 4 | CDN 防护：阿里云 DDoS 高防 IP 接入（控制台）| 运维 | 15min |
| 5 | 黑名单：Nginx 配置 `deny <malicious_ip>;` | 运维 | 10min |
| 6 | 必要时切静态页：`docker compose stop api`，Nginx 返回维护页 | 运维 | 5min |
| 7 | 攻击缓解后恢复限流配置 | 运维 | 验证后 |

**Nginx 黑名单示例**：
```nginx
# frontend/nginx.conf
deny 1.2.3.4;      # 恶意 IP
deny 5.6.0.0/16;   // 恶意网段
```

---

## 4. 联系人通讯录

### 4.1 内部联系人

| 角色 | 姓名 | 手机 | 飞书 | 邮箱 | 备注 |
|---|---|---|---|---|---|
| 应急指挥 / 项目负责人 | 邝振华 | ____ | ____ | ____ | 7×24 待命 |
| 运维 | ____ | ____ | ____ | ____ | 值班轮换 |
| 开发 | 邝振华 | ____ | ____ | ____ | 兼任 |
| DBA | ____ | ____ | ____ | ____ | 外部支持 |
| 伦理委员会主席 | ____ | ____ | ____ | ____ | 公平性事件 |

### 4.2 外部联系人

| 机构 | 联系人 | 电话 | 用途 |
|---|---|---|---|
| 阿里云 7×24 支持 | ____ | 95187 转 3 | RDS/Redis 故障 |
| 阿里云 DashScope | ____ | ____ | LLM API 故障 |
| 法律顾问 | ____ | ____ | PIPL 合规 |
| 网信办举报 | ____ | 12377 | 数据泄露报告 |
| 试点客户 HR 联系人 | ____ | ____ | 客户通知 |

### 4.3 升级路径

```
运维（5min 无响应）→ 应急指挥（10min 无响应）→ 法律顾问（仅 P0 合规事件）
```

---

## 5. 演练计划

### 5.1 演练频率

- **季度演练**：每季度 1 次（Q1/Q2/Q3/Q4），覆盖不同场景
- **年度演练**：全场景综合演练 1 次

### 5.2 年度演练计划

| 季度 | 演练场景 | 参与方 | 验收标准 | 负责人 |
|---|---|---|---|---|
| Q3（2026-09）| 场景 3：数据库故障主从切换 | 运维 + DBA | RTO < 1h | 邝振华 |
| Q4（2026-12）| 场景 6：PII 泄露响应 | 全体 + 法律顾问 | 72h 报告流程走通 | 邝振华 |
| Q1（2027-03）| 场景 1+2：模型漂移 + 公平性超标 | 运维 + 开发 + 伦理委员会 | Kill Switch 自动激活 + 回滚 | 邝振华 |
| Q2（2027-06）| 场景 7：DDoS 攻击 | 运维 | 限流生效 + CDN 接入 | 邝振华 |

### 5.3 演练流程

1. **演练通知**：演练前 3 天通知参与方（生产环境演练需申请窗口）
2. **演练准备**：备份当前状态，准备回滚方案
3. **演练执行**：按场景处置流程执行，记录每步时效
4. **演练验证**：确认系统恢复正常
5. **演练报告**：24h 内输出报告，含时效分析 + 改进项
6. **预案更新**：根据演练结果更新本预案

### 5.4 演练报告模板

```markdown
# HRA 应急演练报告（YYYY-Qn）

## 1. 演练概述
- 演练场景：____
- 演练时间：____
- 参与人员：____

## 2. 演练过程
| 步骤 | 计划时效 | 实际时效 | 偏差 | 说明 |
|---|---|---|---|---|

## 3. 验收
- [ ] RTO 达标
- [ ] 数据完整性
- [ ] 审计日志完整

## 4. 发现的问题
1. ____

## 5. 改进项
| 改进项 | 责任人 | 完成日期 |
|---|---|---|

## 6. 签署
- 演练负责人：____ 日期：____
```

---

## 6. 应急工具箱

### 6.1 一键操作脚本

```bash
# scripts/emergency.sh
#!/bin/bash
case "$1" in
  kill-switch-on)
    curl -X POST http://localhost:8000/api/v1/admin/kill-switch/activate \
      -H "Authorization: Bearer $2" -H "Content-Type: application/json" \
      -d '{"reason":"emergency manual activation"}'
    ;;
  kill-switch-off)
    curl -X POST http://localhost:8000/api/v1/admin/kill-switch/deactivate \
      -H "Authorization: Bearer $2"
    ;;
  stop-external)
    docker compose stop nginx
    echo "External access stopped"
    ;;
  backup-now)
    pg_dump -h $DB_HOST -U hra -Fc -f /backup/pg/emergency_$(date +%Y%m%d_%H%M).dump hra
    echo "Emergency backup created"
    ;;
  status)
    curl -s http://localhost:8000/health | jq .
    docker compose ps
    ;;
  *)
    echo "Usage: $0 {kill-switch-on|kill-switch-off|stop-external|backup-now|status}"
    ;;
esac
```

### 6.2 监控告警规则（Prometheus Alertmanager）

```yaml
# 关键告警规则
groups:
  - name: hra-critical
    rules:
      - alert: HRAApiDown
        expr: up{job="api"} == 0
        for: 1m
        labels: { severity: P0 }
        annotations: { summary: "API 容器不可用" }

      - alert: HRADatabaseUnhealthy
        expr: hra_health_database == 0
        for: 1m
        labels: { severity: P0 }
        annotations: { summary: "数据库不健康" }

      - alert: HRAKillSwitchActive
        expr: hra_kill_switch_active == 1
        for: 0m
        labels: { severity: P1 }
        annotations: { summary: "Kill Switch 已激活" }

      - alert: HRAFairnessExceeded
        expr: hra_fairness_max_parity > 0.05
        for: 5m
        labels: { severity: P1 }
        annotations: { summary: "公平性偏差超标" }

      - alert: HRADriftCritical
        expr: hra_drift_max_psi > 0.2
        for: 5m
        labels: { severity: P1 }
        annotations: { summary: "模型漂移 critical" }
```

---

## 7. 复盘与改进

### 7.1 事件复盘流程

每次 P0/P1 事件解决后 72h 内召开复盘会：

1. **时间线还原**：从发现到解决的完整时间线
2. **根因分析**：5 Why 分析法
3. **影响评估**：业务影响 / 数据影响 / 合规影响
4. **改进项**：流程改进 / 技术改进 / 预案改进
5. **责任认定**：明确责任与改进责任人
6. **报告归档**：`ops/incidents/INC-YYYYMMDD-NNN.md`

### 7.2 复盘报告模板

```markdown
# HRA 事件复盘报告（INC-YYYYMMDD-NNN）

## 1. 事件概述
- 事件 ID：INC-YYYYMMDD-NNN
- 级别：P0/P1/P2
- 发生时间：____
- 解决时间：____
- 持续时长：____

## 2. 时间线
| 时间 | 事件 | 操作人 |
|---|---|---|

## 3. 根因分析
5 Why：
1. Why ____ → ____
2. Why ____ → ____
...

## 4. 影响评估
- 业务影响：____
- 数据影响：____
- 合规影响：____

## 5. 改进项
| 改进项 | 类型 | 责任人 | 完成日期 |
|---|---|---|---|

## 6. 签署
- 项目负责人：____ 日期：____
```

---

## 附录：变更记录

| 版本 | 日期 | 变更人 | 变更内容 |
|---|---|---|---|
| V1.0 | 2026-07-27 | 邝振华 | 初稿创建（7 类应急场景 + 演练计划）|
