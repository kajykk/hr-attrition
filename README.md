# HRA - 企业员工离职风险与人才流失预警系统

多模态融合（结构化 + 行为 + 文本）离职风险预测平台。技术栈：Vue 3 + TypeScript + Vite / FastAPI + SQLAlchemy(async) + Celery + Redis + PostgreSQL + LightGBM + SHAP。

完整文档体系见 [docs/README.md](docs/README.md)（D01-D11）。

## 目录结构

```
backend/   FastAPI 后端（api / models / services / ml / alembic）
frontend/  Vue 3 前端（vite + pinia + vue-router）
docs/      项目文档（D01-D11）
docker-compose.yml  本地一键编排（api/worker/beat/postgres/redis/nginx/prometheus/grafana/loki）
```

## 本地开发

### 后端

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -e ".[dev]"
cp .env.example .env                             # 按需修改
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

- API 文档：http://localhost:8000/docs
- 数据库迁移（生产/正式环境）：`alembic upgrade head`；开发环境启动时自动 `create_all` 兜底
- 测试：`.venv\Scripts\python.exe -m pytest tests -q`（280+ 用例，全部 mock 不依赖真实 PG）

### 前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173，/api 代理到 :8000
npm run build      # vue-tsc 类型检查 + vite build
npm run lint       # ESLint flat config（eslint.config.js）
```

## 关键设计约定

- **多租户**：所有业务表含 `tenant_id`，行级隔离（ADR-002），查询必须过滤
- **PII 加密**：姓名/身份证/手机号/薪资等 Fernet 加密入库 + SHA256 检索哈希（ADR-007）
- **RBAC**：路由层 `require_role` 强制角色（admin/hr_manager/hrbp/manager）
- **2FA**：管理员登录强制 TOTP（pyotp），登录限流默认 `5/minute`（`RATE_LIMIT_LOGIN`）
- **特征契约**：推理侧与训练侧特征列/分位定义一致性由 `assert_feature_contract` 校验（P1-5）
- **审计**：Kill Switch / 登录 / 预警操作写哈希链审计日志（`append_audit_log`）
- **健康检查**：`/health` 真实探测 DB/Redis，返回 `healthy | degraded`，不硬编码
- **日志**：`LOG_FORMAT=json` 输出结构化日志，全链路 `X-Request-ID` 追踪

## 生产部署要点

1. `APP_ENV=production`（禁用 create_all，必须 `alembic upgrade head`）
2. `LOG_FORMAT=json` + Loki/Promtail 采集（docker-compose 已内置）
3. 强制更换 `JWT_SECRET` / `PII_FERNET_KEY` / DB 密码（见 `.env.example`）
4. nginx 已为 SSE 端点 `/api/v1/advise/stream` 关闭代理缓冲
5. Kill Switch 激活后预测返回安全降级（risk_score=50），写审计日志

## 测试/质量

| 命令 | 说明 |
|---|---|
| `pytest tests -q`（backend） | 后端全量测试 |
| `npm run build`（frontend） | 类型检查 + 构建 |
| `npm run lint`（frontend） | ESLint |
