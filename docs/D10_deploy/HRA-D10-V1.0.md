# HRA-D10 上线部署方案

| 项 | 值 |
|---|---|
| 文档编号 | HRA-D10-V1.0 |
| 文档版本 | V1.1 |
| 编制日期 | 2026-07-27 |
| 文档状态 | 草稿 |
| 部署模式 | Docker Compose 单机（MVP）|

---

## 1. 部署架构

### 1.1 生产环境拓扑

MVP 阶段单可用区部署（阿里云 ECS）：

```
┌──────────────────────────────────────────────────────────┐
│              公网（HTTPS 443 / WSS 443）                  │
│                          ↓                               │
│                  Nginx 反向代理                           │
│                          ↓                               │
│  ┌──────────────────────────────────────────────────┐  │
│  │            Docker Compose 网络（hra-net）         │  │
│  │                                                   │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐         │  │
│  │  │ api ×2  │  │worker×2 │  │ beat ×1 │         │  │
│  │  └────┬────┘  └────┬────┘  └────┬────┘         │  │
│  │       │            │            │               │  │
│  │  ┌────┴────────────┴────────────┴────┐          │  │
│  │  │      Redis（主从 + Sentinel）     │          │  │
│  │  └────────────────────────────────────┘          │  │
│  │  ┌────────────────────────────────────┐          │  │
│  │  │   PostgreSQL（主从 + WAL 归档）    │          │  │
│  │  └────────────────────────────────────┘          │  │
│  │  ┌────────────────────────────────────┐          │  │
│  │  │   监控栈（Prometheus + Grafana）   │          │  │
│  │  └────────────────────────────────────┘          │  │
│  └───────────────────────────────────────────────────┘  │
│                          ↓                               │
│                  阿里云 OSS（备份）                       │
└──────────────────────────────────────────────────────────┘
                          ↓
                  外部服务：OpenAI API
```

### 1.2 网络架构

| 网络 | 类型 | 用途 |
|---|---|---|
| 公网 | HTTPS/WSS | 用户访问 |
| hra-net | Docker bridge | 内部服务通信 |
| VPC 私网 | 阿里云 VPC | DB/Redis 跨机通信 |

### 1.3 容器编排

MVP 阶段使用 Docker Compose（单机），V2.0 升级 K3s（轻量 Kubernetes）。

---

## 2. 部署清单

### 2.1 服务清单

| 服务 | 镜像 | 副本 | CPU | 内存 | 端口 |
|---|---|---|---|---|---|
| nginx | nginx:1.27-alpine | 1 | 0.5c | 512M | 80/443 |
| api | hra-api:v1.0 | 2 | 1c | 1G | 8000 |
| worker | hra-worker:v1.0 | 2 | 1c | 1G | - |
| beat | hra-worker:v1.0 | 1 | 0.5c | 512M | - |
| postgres | 阿里云 RDS PostgreSQL 15（高可用版）| 主备 | 2c | 4G | 5432（内网）|
| redis | 阿里云 Redis 7 托管（主备版）| 主备 | 1c | 1G | 6379（内网）|
| prometheus | prom/prometheus:v2.55.0 | 1 | 0.5c | 512M | 9090 |
| grafana | grafana/grafana:11.5.0 | 1 | 0.5c | 512M | 3000 |
| loki | grafana/loki:3.0 | 1 | 0.5c | 512M | 3100 |
| promtail | grafana/promtail:3.0 | 1 | 0.2c | 256M | - |

总资源需求：约 12 CPU / 16 GB 内存

### 2.2 配置项清单

#### 环境变量（.env）

```env
# 数据库
POSTGRES_USER=hra
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=hra
DATABASE_URL=postgresql+asyncpg://hra:<pwd>@postgres:5432/hra

# Redis
REDIS_URL=redis://redis:6379/0
REDIS_PASSWORD=<strong-password>

# JWT
JWT_SECRET=<64-char-random>
JWT_ALGORITHM=HS256
JWT_ACCESS_EXPIRE_MINUTES=30
JWT_REFRESH_EXPIRE_DAYS=7

# 加密
PII_FERNET_KEY=<fernet-key>
PII_KEY_ROTATION_DAYS=90

# LLM（主：通义千问 Max）
DASHSCOPE_API_KEY=<sk-...>
LLM_PRIMARY=qwen-max
LLM_FALLBACK=deepseek-v3
LLM_OPTIONAL=gpt-4-turbo
OPENAI_API_KEY=<sk-...>  # 默认禁用，需数据出境评估通过
LLM_TIMEOUT=30
LLM_MAX_RETRIES=3

# 邮件
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USER=...
SMTP_PASSWORD=...

# 短信
SMS_PROVIDER=aliyun
SMS_ACCESS_KEY=...
SMS_SECRET_KEY=...

# OSS
OSS_ACCESS_KEY=...
OSS_SECRET_KEY=...
OSS_BUCKET=hra-backup
OSS_REGION=oss-cn-hangzhou

# 监控
METRICS_ACCESS_TOKEN=<bearer-token>
SENTRY_DSN=<sentry-dsn>

# 业务
PASSWORD_RESET_BASE_URL=https://hra.example.com
CORS_ORIGINS=https://hra.example.com
RATE_LIMIT_LOGIN=5/minute
RATE_LIMIT_API=100/minute
```

#### Secret 管理

| Secret 类型 | 存储 | 轮换周期 |
|---|---|---|
| 数据库密码 | .env + 阿里云 KMS | 季度 |
| Redis 密码 | .env + KMS | 季度 |
| JWT Secret | .env + KMS | 半年 |
| Fernet Key | KMS + 版本化 | 季度 |
| OpenAI Key | .env + KMS | 半年 |
| SMTP/SMS | .env | 半年 |

### 2.3 数据库/缓存/MQ 部署

#### PostgreSQL

- 版本：15-alpine
- 配置：`shared_buffers=2GB` / `work_mem=64MB` / `max_connections=200`
- 主从：流复制（异步）
- WAL 归档：每小时推送至 OSS

#### Redis

- 版本：7-alpine
- 持久化：RDB（每 5min）+ AOF（每秒 fsync）
- 主从 + Sentinel 自动故障转移
- 最大内存：1GB + LRU 淘汰

---

## 3. 部署流程

### 3.1 CI/CD Pipeline

```
GitHub Push → Actions：
  1. 代码检出
  2. 依赖安装（pip/npm）
  3. Lint（ruff/eslint）
  4. 单元测试（pytest/vitest）
  5. 构建 Docker 镜像
  6. 镜像扫描（trivy）
  7. 推送至阿里云容器镜像服务
  8. 部署至 Staging（main 分支）
  9. 手动审批 → 部署至 Prod（tag 发布）
```

### 3.2 部署步骤

#### 3.2.1 首次部署

```bash
# 1. 拉取代码
git clone https://github.com/kajykk/hr-attrition.git
cd hr-attrition

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入真实 Secret

# 3. 初始化数据库
docker compose run --rm api python -m alembic upgrade head
docker compose run --rm api python scripts/init_db.py

# 4. 加载种子数据
docker compose run --rm api python scripts/seed_data.py

# 5. 启动全部服务
docker compose up -d

# 6. 健康检查
curl https://hra.example.com/api/v1/admin/health
```

#### 3.2.2 迭代部署（蓝绿/金丝雀）

```bash
# 1. 拉取新镜像
docker compose pull api worker beat

# 2. 启动新版本（不停止旧版本）
docker compose up -d --no-deps --scale api=4 api

# 3. Nginx 切换部分流量至新版本（金丝雀 5%）
./scripts/canary_5pct.sh

# 4. 观察 30min
./scripts/monitor_canary.sh

# 5. 全量切换 + 停止旧版本
./scripts/canary_100pct.sh
docker compose up -d --no-deps --scale api=2 api
```

### 3.3 数据库迁移流程

```bash
# 1. 备份
pg_dump -h prod-db -U hra hra > backup_$(date +%Y%m%d).sql

# 2. Staging 验证
alembic upgrade head

# 3. 生产执行（在线 DDL）
alembic upgrade head
# 使用 CREATE INDEX CONCURRENTLY / NOT VALID + VALIDATE

# 4. 回滚（如需）
alembic downgrade -1
```

### 3.4 流量切换与验证

| 阶段 | 流量比例 | 观察期 | 准入条件 |
|---|---|---|---|
| 灰度 1 | 5% | 30min | 错误率 < 0.1% / P99 < 1s |
| 灰度 2 | 25% | 2h | 错误率 < 0.1% / P99 < 1s |
| 灰度 3 | 50% | 4h | 同上 + 公平性偏差 < 5% |
| 全量 | 100% | - | 同上 + 模型 AUC 不下降 |

---

## 4. 回滚方案

### 4.1 回滚触发条件

| 条件 | 阈值 | 自动/手动 |
|---|---|---|
| API 错误率 | > 1% | 自动 |
| API P99 延迟 | > 2s | 自动 |
| 模型推理失败率 | > 5% | 自动 |
| 公平性偏差 | > 8% | 自动（Kill Switch） |
| 业务指标异常 | 客户投诉 | 手动 |

### 4.2 回滚步骤

#### 自动回滚（金丝雀阶段）

```bash
# 1. 自动触发（celery_beat 每 30s 检查）
# 2. Nginx 切回旧版本
./scripts/rollback_canary.sh
# 3. 通知管理员
```

#### 手动回滚（全量后）

```bash
# 1. 切回旧镜像
docker compose up -d --no-deps api=v0.9 worker=v0.9 beat=v0.9

# 2. 数据库回滚（前向修复优先）
alembic downgrade -1

# 3. 验证服务
curl https://hra.example.com/api/v1/admin/health

# 4. 通知用户
python scripts/notify_users.py --message "系统已回滚至上一版本"
```

### 4.3 回滚时效

| 场景 | RTO |
|---|---|
| 金丝雀自动回滚 | < 5min |
| 手动回滚（无 DB 变更） | < 15min |
| 手动回滚（含 DB 回滚） | < 30min |
| 数据误删恢复 | < 1h（从备份） |

### 4.4 数据回滚策略

- **优先前向修复**：通过新版本修复 bug，不回滚数据
- **数据库回滚**：仅在下策时使用，需停服 + 数据丢失风险
- **备份恢复**：每日全量备份 + WAL 增量，可恢复至任意时间点

---

## 5. 运维交接

### 5.1 Runbook 清单

| Runbook | 用途 |
|---|---|
| 日常巡检 Runbook | 每日检查项 |
| 数据库故障 Runbook | 主库切换 |
| Redis 故障 Runbook | Sentinel 切换 |
| LLM 不可用 Runbook | 降级处理 |
| 公平性偏差 Runbook | Kill Switch 处理 |
| 数据泄露 Runbook | 应急响应 |

### 5.2 监控告警接管清单

| 告警 | 阈值 | 接收人 | 升级 |
|---|---|---|---|
| API 5xx | > 1% | 邝振华 | 30min 未处理 → 电话 |
| API P99 | > 2s | 邝振华 | 1h 未处理 → IM |
| DB 连接池 | > 80% | 邝振华 | 即时 |
| Redis 内存 | > 80% | 邝振华 | 即时 |
| 公平性偏差 | > 5% | 邝振华 + 伦理委员会 | 即时 + Kill Switch |
| 模型 AUC 下降 | > 5% | 邝振华 | 1h |
| LLM 错误率 | > 5% | 邝振华 | 自动降级 |

### 5.3 值班制度

| 时段 | 值班人 | 响应时效 |
|---|---|---|
| 工作日 09:00-18:00 | 邝振华 | 30min |
| 工作日夜间 | 邝振华（手机）| 2h |
| 周末 | 邝振华（手机）| 4h |
| 法定节假日 | 邝振华（手机）| 8h |

P0 告警任何时候 2h 内响应。

---

## 6. 安全加固

### 6.1 镜像扫描

- Trivy 扫描所有镜像
- 严重漏洞（Critical/High）阻断部署
- 基础镜像定期更新（月度）

### 6.2 Secret 轮换

| Secret | 轮换周期 | 流程 |
|---|---|---|
| 数据库密码 | 季度 | 双密码过渡（先加新、后删旧） |
| Fernet Key | 季度 | 双密钥过渡（先加密、后解密） |
| JWT Secret | 半年 | Refresh Token 失效 + 重新登录 |
| OpenAI Key | 半年 | 直接替换 |

### 6.3 网络策略

- 公网仅开放 80/443
- DB/Redis 仅 VPC 私网访问
- 容器间通信使用 Docker 内部网络
- 限制出站：仅允许 OpenAI/OSS/SMTP/SMS API

### 6.4 上线前安全 Checklist

- [ ] 所有 Secret 已通过 KMS 管理
- [ ] TLS 证书有效（Let's Encrypt 自动续期）
- [ ] 镜像扫描无 Critical/High
- [ ] 限流配置生效
- [ ] CSP / XSS / SQL 注入防护启用
- [ ] 审计日志写入正常
- [ ] 备份恢复演练通过
- [ ] 渗透测试无 Critical 漏洞
- [ ] PIPL 合规自评通过
- [ ] 伦理委员会审批通过

---

## 7. HRA 特殊部署要点

### 7.1 模型版本灰度

复用 DWS 金丝雀引擎，三阶段发布：

| 阶段 | 流量 | 观察期 | 准入 |
|---|---|---|---|
| 1 | 5% | 24h | AUC 下降 < 2% / 错误率 < 0.5% |
| 2 | 25% | 24h | 同上 + 公平性偏差 < 5% |
| 3 | 100% | 24h | 同上 + 客户反馈无异常 |

### 7.2 数据备份与异地容灾

- 数据库：每日全量（02:00）+ WAL 持续归档至 OSS
- Redis：每日 RDB + AOF
- OSS：跨区域复制（杭州 → 上海）
- 备份保留：30 天滚动

### 7.3 公平性监控部署

- 每日 03:00 计算公平性指标
- 偏差 > 5% 触发 P0 告警 + Kill Switch 自动激活
- 月度公平性报告生成 + 伦理委员会审阅

### 7.4 LLM 调用治理

- 主 LLM：通义千问 Max（阿里云 DashScope，规避跨境传输）
- 备用 LLM：DeepSeek-V3（通义千问不可用时切换）
- 可选 LLM：OpenAI GPT-4（需数据出境评估通过后启用，默认禁用）
- 限流：10 次/分钟/用户
- 月度预算上限：1000 元（超出降级）
- 调用日志审计（含 prompt 脱敏检查）

---

## 8. 灾备演练

### 8.1 演练计划

| 演练类型 | 频率 | 范围 |
|---|---|---|
| 数据库主从切换 | 月度 | Staging → Prod |
| Redis Sentinel 切换 | 月度 | 同上 |
| 全量备份恢复 | 季度 | Prod 数据 → 恢复至测试环境 |
| Kill Switch 激活 | 月度 | Staging |
| 完整灾备演练 | 季度 | 模拟可用区故障 |

### 8.2 演练记录模板

```
演练日期：____
演练场景：____
参与人员：____
执行步骤：
1. ____
2. ____
演练结果：成功/失败
RTO 实测：____
RPO 实测：____
问题与改进：____
```

---

## 9. 变更记录

| 版本 | 日期 | 变更人 | 变更内容 | 审核人 |
|---|---|---|---|---|
| V1.0 | 2026-07-27 | 邝振华 | 初稿创建 | - |
| V1.1 | 2026-07-27 | 邝振华 | 设计审查修订：2.1 改阿里云托管 RDS/Redis；7.4 LLM 主选通义千问；2.2 环境变量更新 | - |
