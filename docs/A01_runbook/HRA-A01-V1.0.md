# HRA-A01 运维手册（Runbook）

| 项 | 值 |
|---|---|
| 文档编号 | HRA-A01-V1.0 |
| 文档版本 | V1.0 |
| 编制日期 | 2026-07-27 |
| 编制人 | 邝振华 |
| 文档状态 | 正式 |
| 适用范围 | HRA V1.0 生产环境运维 |
| 关联文档 | D03 系统设计 / D10 部署方案 / A02 应急预案 |

> 本手册覆盖 D08 任务 T-709 要求的 6 份 Runbook：日常巡检 / 数据库运维 / Redis 运维 / LLM 服务 / 公平性监测 / 数据泄露响应。所有命令均在 `e:\code\hr-attrition` 仓库根目录或部署主机执行。

---

## Runbook 1：日常巡检

### 1.1 巡检目标

保障 HRA 生产环境（nginx / api / worker / beat / postgres / redis / prometheus / grafana / loki / promtail）持续可用，提前发现容量与性能异常。

### 1.2 每日巡检（09:00 执行，耗时约 15 分钟）

| 序号 | 巡检项 | 操作命令 / 入口 | 正常范围 | 异常处置 |
|---|---|---|---|---|
| D1 | 健康检查端点 | `curl -s http://localhost:8000/health` | `status=healthy`；database/redis/celery 均 healthy | 见 A02 场景 3/4 |
| D2 | API 容器存活 | `docker compose ps api worker beat` | 状态 `Up` | `docker compose up -d api` |
| D3 | Prometheus 指标 | http://localhost:9090 → `up{job="api"}` | 全部 `1` | 重启对应容器 |
| D4 | Grafana 面板 | http://localhost:3000 → HRA Overview | 无红色告警 | 查 Loki 日志 |
| D5 | Loki 日志错误率 | LogQL：`rate({app="api"} |= "ERROR" [5m])` | < 1 条/秒 | 见 A02 对应场景 |
| D6 | Redis 内存 | `docker compose exec redis redis-cli INFO memory` | `used_memory_rss < 512MB` | 见 Runbook 3 §3.4 |
| D7 | PG 连接数 | `docker compose exec postgres psql -U hra -c "SELECT count(*) FROM pg_stat_activity;"` | < 100（max_connections=200） | 见 Runbook 2 §2.4 |
| D8 | Celery 队列积压 | `docker compose exec redis redis-cli LLEN celery` | < 50 | 扩 worker：`docker compose up -d --scale worker=4` |
| D9 | 磁盘空间 | `docker system df` + `df -h` | 使用率 < 80% | 清理镜像 / 扩容 |
| D10 | Kill Switch 状态 | `curl -s http://localhost:8000/api/v1/admin/kill-switch -H "Authorization: Bearer <token>"` | `active=false` | 见 A02 场景 1/2 |

**每日巡检记录**写入 `ops/daily_checklist_YYYYMMDD.md`，异常项 30 分钟内升级至项目负责人。

### 1.3 每周巡检（周一 10:00 执行，耗时约 30 分钟）

| 序号 | 巡检项 | 操作 | 阈值 | 处置 |
|---|---|---|---|---|
| W1 | 漂移检测报告 | 查 `app/ml/models/drift_report.json`（beat 每日 02:00 生成）| `max_psi < 0.2` | 见 A03 §漂移检测 |
| W2 | 公平性日报 | 查 `app/ml/models/fairness_report.json`（beat 每日 03:00 生成）| `max_parity_difference < 0.05` | 见 Runbook 5 |
| W3 | PG 慢查询 | `SELECT query, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;` | mean_exec_time < 1000ms | 加索引 / 优化 SQL |
| W4 | Redis 大 key | `docker compose exec redis redis-cli --bigkeys` | 无 > 10MB key | 见 Runbook 3 §3.5 |
| W5 | 审计日志哈希链校验 | 执行 `audit_service.verify_chain()` | 全链一致 | 见 A02 场景 6（疑似篡改）|
| W6 | 备份完整性 | 查 OSS `backup/pg/YYYYMMDD/` 最新 dump | 存在且 > 100MB | 见 Runbook 2 §2.2 |
| W7 | 证书有效期 | `echo \| openssl s_client -connect hra.example.com:443 2>/dev/null \| openssl x509 -noout -enddate` | 剩余 > 30 天 | 续签 |
| W8 | LLM 配额余量 | 阿里云 DashScope 控制台 → 用量统计 | 余量 > 20% | 见 Runbook 4 §4.5 |

### 1.4 每月巡检（每月 1 日执行，耗时约 2 小时）

| 序号 | 巡检项 | 操作 | 输出 |
|---|---|---|---|
| M1 | PG 分区维护 | 见 Runbook 2 §2.5 | 创建下月分区、归档 2 年前分区 |
| M2 | 索引膨胀 | `SELECT schemaname, relname, index_scan FROM pg_stat_user_indexes WHERE index_scan=0;` | 清理未使用索引 |
| M3 | 模型指标月报 | 汇总 `fusion_metrics.json` 月度均值 | AUC 月降 < 2% |
| M4 | 伦理委员会月度报告 | 见 Runbook 5 §5.4 | 提交委员会评审 |
| M5 | 容量规划 | Grafana 30 天趋势 + 业务增长率 | 输出扩容建议 |
| M6 | 安全补丁 | `docker compose pull` + `trivy image` | 0 Critical/High |
| M7 | 灾备演练 | 见 A02 §演练计划 | RTO < 1h |

### 1.5 巡检脚本（可选自动化）

```bash
# scripts/daily_check.sh
#!/bin/bash
set -e
echo "=== HRA 日常巡检 $(date) ==="
echo "[1] 健康检查:"
curl -s http://localhost:8000/health | jq .
echo "[2] 容器状态:"
docker compose ps --format "table {{.Name}}\t{{.Status}}"
echo "[3] Redis 内存:"
docker compose exec -T redis redis-cli INFO memory | grep used_memory_rss
echo "[4] Celery 队列:"
docker compose exec -T redis redis-cli LLEN celery
echo "[5] PG 连接:"
docker compose exec -T postgres psql -U hra -t -c "SELECT count(*) FROM pg_stat_activity;"
echo "=== 巡检结束 ==="
```

---

## Runbook 2：数据库运维

### 2.1 PG 架构概览

- **本地开发**：`postgres:15-alpine` 单实例（docker-compose）
- **生产环境**：阿里云 RDS PostgreSQL 15 高可用版，主备流复制 + WAL 归档至 OSS
- **关键参数**：`shared_buffers=2GB` / `work_mem=64MB` / `max_connections=200`
- **数据库表**：见 D04，核心含 `employees`（PII 加密）/ `risk_predictions`（月度分区）/ `audit_logs`（哈希链 + 月度分区）

### 2.2 备份策略

#### 2.2.1 每日全量备份（pg_dump）

```bash
# 每日 01:00 执行（crontab）
pg_dump -h <rds-host> -U hra -Fc -f /backup/pg/hra_$(date +%Y%m%d).dump hra
# 上传 OSS
ossutil cp /backup/pg/hra_$(date +%Y%m%d).dump oss://hra-backup/pg/$(date +%Y%m%d)/
# 保留策略：30 天滚动
find /backup/pg/ -mtime +30 -delete
```

#### 2.2.2 WAL 归档（PITR 基础）

```conf
# postgresql.conf
archive_mode = on
archive_command = 'ossutil cp %p oss://hra-backup/wal/%f'
wal_level = replica
```

#### 2.2.3 验证备份

```bash
# 每周从备份恢复至测试库验证
createdb hra_verify
pg_restore -d hra_verify /backup/pg/hra_$(date +%Y%m%d).dump
psql -d hra_verify -c "SELECT count(*) FROM employees;"
```

### 2.3 恢复流程（PITR）

**场景**：误删数据，需恢复到 2026-09-15 14:30:00 的状态。

```bash
# 1. 关闭 api/worker（停止写入）
docker compose stop api worker beat

# 2. 恢复最近全量备份
pg_restore -h <rds-host> -U hra -d hra -c /backup/pg/hra_20260915.dump

# 3. 应用 WAL 至目标时间点（RDS 控制台或手动）
recovery_target_time = '2026-09-15 14:30:00+08'
restore_command = 'ossutil cp oss://hra-backup/wal/%f %p'

# 4. 数据校验
psql -c "SELECT count(*) FROM employees; SELECT count(*) FROM audit_logs;"

# 5. 重启服务
docker compose up -d api worker beat
```

**RTO 目标**：< 1 小时（S-SEC-07）。

### 2.4 迁移管理（Alembic）

```bash
# 应用迁移至最新
cd backend
alembic upgrade head

# 查看当前版本
alembic current

# 回滚上一版本
alembic downgrade -1

# 生成新迁移（开发环境）
alembic revision --autogenerate -m "add xxx table"
```

**迁移规范**：
- 生产环境迁移前必须备份（§2.2.1）
- 大表 DDL 采用 online 模式（pg_repack）
- 迁移脚本经代码评审后合并

### 2.5 连接池调优

- **asyncpg 池配置**（`app/db/session.py`）：`pool_size=20` / `max_overflow=10` / `pool_pre_ping=True`
- **RDS 侧**：`max_connections=200`，预留 50 给运维 / 备份
- **监控**：`pg_stat_activity` 连接数 > 150 告警

### 2.6 分区维护（risk_predictions / audit_logs）

```sql
-- 创建下月分区（每月 25 日执行）
CREATE TABLE IF NOT EXISTS risk_predictions_202610
    PARTITION OF risk_predictions
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

CREATE TABLE IF NOT EXISTS audit_logs_202610
    PARTITION OF audit_logs
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

-- 归档 2 年前分区（保留策略：预测 2 年 / 审计 5 年）
ALTER TABLE risk_predictions DETACH PARTITION risk_predictions_202410;
-- 迁移至冷存储 OSS
pg_dump -t risk_predictions_202410 -f /archive/risk_predictions_202410.dump
DROP TABLE risk_predictions_202410;
```

**自动化**：写入 Celery beat 月度任务（`app.tasks.db_maintenance`，每月 25 日 04:00）。

---

## Runbook 3：Redis 运维

### 3.1 Redis 角色

- Celery broker + backend
- 预测结果缓存（`risk:cache:{tenant_id}:{employee_id}`，TTL 1h）
- Kill Switch 全局开关（`kill_switch:active` Hash）
- 限流计数器（slowapi）

### 3.2 内存监控

```bash
# 实时内存
docker compose exec redis redis-cli INFO memory | grep -E "used_memory_rss|used_memory_peak|maxmemory"

# 命中率
docker compose exec redis redis-cli INFO stats | grep -E "keyspace_hits|keyspace_misses"

# 各 db key 数量
docker compose exec redis redis-cli INFO keyspace
```

**告警阈值**：`used_memory_rss > 80% maxmemory` → 扩容或清理。

### 3.3 持久化（RDB + AOF）

```conf
# redis.conf（docker-compose 已配置 appendonly yes --save 300 1）
appendonly yes
appendfsync everysec       # 每秒 fsync（性能与安全平衡）
save 300 1                 # 5min 内 1 次变更触发 RDB
save 60 10000              # 1min 内 10000 次变更触发 RDB
```

**故障恢复**：优先加载 AOF（更完整），RDB 作为兜底。

### 3.4 缓存淘汰策略

```conf
maxmemory 512mb
maxmemory-policy allkeys-lru   # LRU 淘汰（缓存场景）
```

**注意**：Kill Switch key（`kill_switch:active`）会被 LRU 淘汰——这是预期行为（fail-open 设计，见 `app/core/kill_switch.py` 注释）。

### 3.5 Kill Switch key 管理

```bash
# 查询状态
docker compose exec redis redis-cli GET kill_switch:active
# 输出示例：{"active":"1","reason":"公平性偏差超标","activated_at":"...","activated_by":"..."}

# 紧急手动激活（仅当 API 不可用时）
docker compose exec redis redis-cli SET kill_switch:active \
  '{"active":"1","reason":"manual-ops","activated_at":"2026-09-15T10:00:00Z","activated_by":"ops"}'

# 解除
docker compose exec redis redis-cli DEL kill_switch:active
```

**优先使用 API 端点**（`POST /api/v1/admin/kill-switch/activate`），自动写审计日志。

### 3.6 大 key 排查

```bash
# 概览
docker compose exec redis redis-cli --bigkeys

# 特定 key 内存
docker compose exec redis redis-cli MEMORY USAGE risk:cache:tenant-001

# 清理无效缓存（谨慎）
docker compose exec redis redis-cli --scan --pattern "risk:cache:*" | head -100
docker compose exec redis redis-cli --scan --pattern "risk:cache:*" | xargs -L 100 redis-cli DEL
```

---

## Runbook 4：LLM 服务运维

### 4.1 LLM 架构

- **主**：通义千问 Max（`qwen-max`，DashScope SSE 接口）
- **备**：DeepSeek-V3（`LLM_FALLBACK`）
- **降级**：规则模板（`app/services/llm_service.py::_fallback_template`）
- **OpenAI**：默认禁用（`OPENAI_ENABLED=false`，需数据出境评估）

调用流程见 `app/services/llm_service.py::LLMService.stream_advice`。

### 4.2 通义千问 API 状态监控

```bash
# 健康检查端点（/health 已含 llm 字段）
curl -s http://localhost:8000/health | jq .components.llm
# 输出：healthy（API key 已配置）/ not_configured（未配置）

# 实时调用测试
curl -X POST http://localhost:8000/api/v1/advise/stream \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"employee_id":"<uuid>","risk_score":75,"shap_factors":[]}'
```

### 4.3 SSE 延迟监控

- **指标**：`llm_first_token_latency_ms`（首字延迟）
- **目标**：P99 < 2000ms（P-PERF-04）
- **采集**：`app/services/llm_service.py` 记录每次调用首字时间戳，上报 Prometheus
- **Grafana 面板**：HRA Overview → LLM Latency

### 4.4 降级模板触发条件

| 触发条件 | 行为 | 日志特征 |
|---|---|---|
| `DASHSCOPE_API_KEY` 未配置 | 直接走规则模板 | `通义千问 Max 调用失败` |
| 主模型超时（30s）| 切 DeepSeek-V3 | `尝试备用 LLM` |
| 备用也失败 | 规则模板 | `降级规则模板` |
| 网络异常 | 规则模板 | `降级规则模板` |

**降级模板内容**：见 `app/services/llm_service.py::_fallback_template`，按 SHAP 归因因子（salary_percentile / promotion_gap_months）生成调薪 / 晋升 / 辅导三类建议。

### 4.5 API key 轮换与配额管理

- **轮换周期**：90 天（与 PII key 同步）
- **轮换流程**：
  1. 阿里云 DashScope 控制台创建新 key
  2. 更新 `.env` 的 `DASHSCOPE_API_KEY`
  3. `docker compose up -d api worker` 滚动重启
  4. 旧 key 观察 7 天后吊销
- **配额监控**：阿里云控制台 → 用量统计，余量 < 20% 告警（周巡检 W8）
- **预算**：首年 3000 元（D01 §6.1）

### 4.6 LLM 故障处置

```bash
# 1. 确认降级生效（应看到 rule-template 元数据）
docker compose logs api | grep "rule-template"

# 2. 排查 DashScope 限流
docker compose logs api | grep "429"

# 3. 临时切备用模型
# 修改 .env: LLM_PRIMARY=deepseek-v3
docker compose up -d api

# 4. 全部 LLM 不可用时，系统自动走规则模板，不影响业务
```

---

## Runbook 5：公平性监测

### 5.1 公平性监测目标

- **4 维度**：gender(M/F) / age(<35 vs >=35) / ethnicity(汉族/少数民族) / disability(0/1)
- **指标**：demographic parity difference（各组"高风险预测"率之差最大绝对值）
- **阈值**：< 5% 正常 / 5-8% 告警 / > 8% Kill Switch
- **高风险定义**：`risk_score >= 60`（见 `app/ml/fairness_test.py`）

### 5.2 每日公平性日报检查

**生成时机**：Celery beat 每日 03:00（`app/celery_app.py::fairness-report-daily`）。

```bash
# 查看最新报告
cat backend/app/ml/models/fairness_report.json | jq .

# 关键字段
{
  "final_threshold": 60,
  "mitigation_applied": false,
  "dimensions": {
    "gender":      {"parity_difference": 0.0210, "passed": true},
    "age":         {"parity_difference": 0.0180, "passed": true},
    "ethnicity":   {"parity_difference": 0.0320, "passed": true},
    "disability":  {"parity_difference": 0.0150, "passed": true}
  },
  "max_parity_difference": 0.0320,
  "overall_passed": true
}
```

### 5.3 偏差阈值响应

| 偏差范围 | 级别 | 响应动作 | 时效 |
|---|---|---|---|
| < 5% | 正常 | 记录日报 | - |
| 5% - 8% | 告警 | 邮件通知项目负责人 + 伦理委员会；排查特征分布 | 24h 内排查 |
| > 8% | 严重 | **Kill Switch 自动激活**；启动公平性重训；伦理委员会紧急审查 | 立即 |

**Kill Switch 自动激活**：当 `max_parity_difference > 0.08`，`app/tasks/model_governance` 任务调用 `kill_switch.activate(reason="公平性偏差超标")`。

### 5.4 公平性重训流程

```bash
# 1. 拉取最新训练数据（含 PII 审计字段）
cd backend
python -m app.ml.train_pipeline

# 2. 执行公平性测试
python -m app.ml.fairness_test
# 自动尝试阈值调整缓解（50-70 搜索最优）

# 3. 验证 4 维度均 < 5%
cat app/ml/models/fairness_report.json | jq .overall_passed
# 期望 true

# 4. 金丝雀发布新模型（见 A03 §模型生命周期）
```

### 5.5 伦理委员会月度报告

**报告模板**（每月 1 日提交）：

```markdown
# HRA 伦理审查月度报告（YYYY-MM）

## 1. 公平性指标月度汇总
| 维度 | 月初偏差 | 月末偏差 | 趋势 | 达标 |
|---|---|---|---|---|
| 性别 | 2.1% | 2.3% | 稳定 | ✓ |
| 年龄 | 1.8% | 2.0% | 稳定 | ✓ |
| 民族 | 3.2% | 3.0% | 改善 | ✓ |
| 残障 | 1.5% | 1.6% | 稳定 | ✓ |

## 2. Kill Switch 事件
- 本月激活次数：0

## 3. 漂移检测结果
- max_psi 月度均值：0.05（stable）

## 4. 禁用特征审计
- 训练数据扫描：gender/ethnicity/disability/birth_date 0 入模 ✓

## 5. 申诉处理
- 本月员工申诉数：0

## 6. 委员会意见
______

## 7. 签署
- 委员会主席：____ 日期：____
```

### 5.6 公平性字段合规约束

> **重要**：`ethnicity` / `disability` 字段仅用于公平性审计计算，**绝不作为模型特征输入**（M-MODEL-10 双重保障）。详见 `app/models/employee.py` 注释与 D11 §3.3 公平性字段说明。员工单独同意采集，Fernet 加密存储。

---

## Runbook 6：数据泄露响应

### 6.1 响应目标

- **PIPL 合规**：72 小时内向网信办报告（PIPL 第 57 条）
- **RTO**：4 小时内隔离泄露源（P0 级别）
- **取证**：保留完整证据链，哈希链审计日志不可篡改

### 6.2 泄露发现 → 隔离（0-1h）

| 步骤 | 操作 | 责任人 |
|---|---|---|
| 1 | 确认泄露迹象（异常审计日志 / 外部举报 / 监控告警） | 运维 |
| 2 | 立即激活 Kill Switch：`POST /api/v1/admin/kill-switch/activate` body `{"reason":"疑似PII泄露"}` | 运维 |
| 3 | 切断外网访问：`docker compose stop nginx` | 运维 |
| 4 | 冻结涉事账号：`UPDATE users SET is_active=false WHERE id='<uuid>'` | DBA |
| 5 | 保留现场：禁止重启容器，截取 `docker compose logs` 全量日志 | 运维 |
| 6 | 通知项目负责人（邝振华）+ 法律顾问 | 运维 |

### 6.3 取证（1-4h）

```bash
# 1. 导出审计日志（哈希链校验）
pg_dump -t audit_logs -f /forensic/audit_logs_$(date +%Y%m%d).dump

# 2. 哈希链完整性校验
python -c "from app.services.audit_service import verify_chain; print(verify_chain())"
# 任一断裂 → 标记为篡改证据

# 3. 提取涉事时间窗口的访问记录
psql -c "SELECT * FROM audit_logs WHERE created_at BETWEEN '2026-09-15 00:00' AND '2026-09-15 12:00' AND action LIKE 'pii.%';"

# 4. Redis 操作日志
docker compose exec redis redis-cli MONITOR > /forensic/redis_ops.log

# 5. 容器镜像快照
docker commit hra-api-1 hra-api-forensic:$(date +%Y%m%d)
```

### 6.4 报告（4-72h）

**内部报告**（4h 内）：

| 项 | 内容 |
|---|---|
| 泄露时间 | 2026-09-15 10:30（UTC+8）|
| 发现时间 | 2026-09-15 11:00 |
| 泄露数据类型 | 员工姓名 / 身份证号 / 手机号（PII 加密字段）|
| 泄露数据量 | 约 500 条 |
| 泄露途径 | 涉事 HR 账号越权导出 |
| 影响范围 | 租户 A 的 500 名员工 |
| 应急措施 | Kill Switch 已激活；账号已冻结；外网已切断 |

**网信办报告**（72h 内，PIPL 第 57 条）：

```markdown
# 个人信息泄露事件报告

## 1. 事件概述
- 报告单位：____
- 事件发生时间：____
- 报告时间：____
- 涉及个人信息主体数量：____

## 2. 事件经过
____

## 3. 原因分析
____

## 4. 危害评估
____

## 5. 已采取的处置措施
____

## 6. 后续整改计划
____

## 7. 联系人
- 项目负责人：邝振华 ____
- 法律顾问：____
```

**当事人通知**（72h 内，PIPL 第 57 条）：通过邮件 / 短信告知受影响员工。

### 6.5 补救与复盘

| 步骤 | 操作 | 责任人 |
|---|---|---|
| 1 | 修复泄露根因（如权限漏洞 / 注入点） | 开发 |
| 2 | 轮换 PII Fernet 密钥（`PII_FERNET_KEY`）+ 重新加密全量 PII | DBA |
| 3 | 轮换 JWT 密钥 + 强制全量用户重置密码 | 开发 |
| 4 | 渗透测试复测（ZAP + 人工） | QA |
| 5 | 解除 Kill Switch（`POST /api/v1/admin/kill-switch/deactivate`）| 项目负责人 |
| 6 | 编写事故复盘报告（root cause / 改进措施 / 责任追究）| 项目负责人 |
| 7 | 更新本 Runbook + A02 应急预案 | 运维 |

### 6.6 证据保留

- 审计日志：5 年保留（`audit_logs` 表，分区归档）
- 取证快照：OSS `forensic/` 目录，7 年保留
- 报告文档：纸质 + 电子双归档

---

## 附录 A：运维联系人

| 角色 | 姓名 | 手机 | 邮箱 | 职责 |
|---|---|---|---|---|
| 项目负责人 | 邝振华 | ____ | ____ | P0 决策 / 外部沟通 |
| 运维 | ____ | ____ | ____ | 日常巡检 / 应急处置 |
| DBA | ____ | ____ | ____ | 数据库运维 |
| 法律顾问 | ____ | ____ | ____ | PIPL 合规 / 泄露报告 |
| 伦理委员会主席 | ____ | ____ | ____ | 公平性审查 |

## 附录 B：关键文件路径速查

| 用途 | 路径 |
|---|---|
| 健康检查 | `app/main.py::health` → `/health` |
| Kill Switch 实现 | `app/core/kill_switch.py` |
| Kill Switch API | `app/api/v1/admin.py` → `/admin/kill-switch/*` |
| 漂移检测 | `app/ml/drift_detector.py` + `app/tasks/model_governance::detect_drift` |
| 公平性测试 | `app/ml/fairness_test.py` + `app/tasks/model_governance::fairness_daily_report` |
| PII 加密 | `app/core/pii_crypto.py` |
| LLM 服务 | `app/services/llm_service.py` |
| 融合引擎 | `app/ml/fusion_engine.py` |
| Celery 调度 | `app/celery_app.py`（beat: 漂移 02:00 / 公平性 03:00）|
| 审计日志模型 | `app/models/audit_log.py`（哈希链）|
| 员工模型 | `app/models/employee.py`（6 个 PII 加密字段）|
| 部署编排 | `docker-compose.yml` |

## 附录 C：变更记录

| 版本 | 日期 | 变更人 | 变更内容 |
|---|---|---|---|
| V1.0 | 2026-07-27 | 邝振华 | 初稿创建（6 份 Runbook）|
