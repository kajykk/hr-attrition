# HRA · 企业员工离职风险预警系统（HR Attrition Risk Analysis）

> 面向企业 HR 的**员工离职风险智能预警平台**：多模态融合（结构化 + 行为时序）预测员工离职概率，从预测 → 解释（SHAP）→ 分级预警 → AI 留存建议形成完整闭环，内置模型治理（漂移 / 公平性 / 回滚）与隐私合规（PII 加密 / PIPL）。

[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688)](https://fastapi.tiangolo.com/)
[![Vue 3](https://img.shields.io/badge/Vue_3+TS-Vite-42b883)](https://vuejs.org/)
[![ML](https://img.shields.io/badge/LightGBM+SHAP-ML-ff69b4)](https://lightgbm.readthedocs.io/)
[![Tests](https://img.shields.io/badge/Tests-314+-green)](backend/tests)

---

## 项目简介

传统 HR 离职分析停留在事后统计，HRA 将机器学习引入人事决策：基于结构化员工档案与 12 个月行为时序（邮件 / 会议 / 登录）预测员工未来离职风险，输出可解释的风险归因与分级预警，并联动智能留存建议（SSE 流式生成）。

## 核心亮点

| 能力 | 实现 |
|---|---|
| **多模态融合预测** | 结构化（LightGBM，AUC 0.9353）+ 行为时序（IsolationForest）融合评分（0.7 / 0.3），**测试 AUC 0.9862**，Top-20% 离职召回 0.8832 |
| **可解释 AI** | SHAP TreeExplainer 个体预测 Top-3 归因（方向 + 贡献值），30 天全局 Top-10 特征画像，特征契约校验（推理与训练特征一致性） |
| **特征真实性** | 推理侧 20 项结构化特征**真实字段优先**（HR 采集），缺失回退训练分布中位/众数常量——**无任何随机注入**，预测结果仅由真实数据决定（0002 迁移） |
| **分级预警闭环** | P0/P1/P2 三级预警 + 状态机（new→confirmed→review→fixing→closed / appealing），24h/48h/72h 升级机制，P0 强制处置路径 |
| **模型治理** | PSI/KL 逐特征漂移检测（0.1/0.2 阈值）；性别/年龄/民族/残障**四维公平性监测**（差异 >8% 自动触发）；Kill Switch 一键熔断降级（返回安全基线预测） |
| **隐私与合规** | Fernet **字段级加密**（姓名/身份证/手机号/薪资等 6 字段）+ SHA256 检索哈希 + 季度密钥轮换；PIPL 数据保留清理（离职 ≥2 年自动清除）；AI 提示词 PII 脱敏 |
| **安全体系** | RBAC 五角色（admin / hr_manager / hrbp / manager / employee）+ 管理员**强制 TOTP 2FA**、登录限流（5/min）、SHA256 审计哈希链 |
| **实时与 AI** | WebSocket 实时风险推送（租户隔离）；AI 留存建议 SSE 流式输出（Qwen-Max → DeepSeek → 规则模板三级回退） |
| **工程化** | 314+ 测试用例、多租户行级隔离、Alembic 迁移、Celery Beat 定时治理任务、Prometheus + Grafana + Loki 可观测、JSON 结构化日志全链路追踪 |

## 模型效果（测试集 10,000 条）

| 模型 | 指标 | 数值 |
|---|---|---|
| 结构化 LightGBM | AUC | **0.9353** |
| 融合模型（0.7 结构化 + 0.3 行为） | AUC | **0.9862** |
| 融合模型 | Top-20% 离职召回率 | **0.8832** |
| 融合模型 | 整体准确率 | 0.9066 |
| 公平性监测（4 维度） | 最大组间风险率差异 | 0.032（目标 <0.05） |

训练数据：以 IBM 员工数据分布为蓝本，SDV GaussianCopula 合成 50,000 条（含 12 个月行为时序），标签生成严格排除性别/民族/残障等敏感字段，从源头保障公平性。

## 系统架构

```
                ┌───────────────────────────────────┐
                │  前端 Vue 3 + TS + Element Plus      │
                │  驾驶舱 · 员工档案 · 风险预测 · 预警中心  │
                │  AI 建议(SSE) · 模型治理 · TOTP 登录    │
                └───────────────┬───────────────────┘
                                │ REST / WebSocket / SSE
┌───────────────────────────────▼──────────────────────────────┐
│  HRA Backend (FastAPI)                                         │
│  · 风险预测: 特征契约 → LightGBM+IsolationForest → SHAP 归因    │
│  · 预警状态机 · 审计哈希链 · RBAC+2FA · 多租户隔离 · 限流        │
│  · AI 建议 (LLM 三级回退 + PII 脱敏)                            │
│  · Celery: 漂移检测 · 公平性报告 · PIPL 数据清理 · 模型回滚       │
└────────────┬───────────────────────────────┬─────────────────┘
             │                               │
┌────────────▼────────────┐    ┌────────────▼─────────────┐
│  PostgreSQL 15 (Alembic) │    │  Redis 7 (缓存/限流/Kill SW) │
│  字段级 PII 加密存储        │    └────────────┬─────────────┘
└──────────────────────────┘                 │
┌──────────────────────────┐    ┌────────────▼─────────────┐
│  可观测性: Prometheus ·      │    │  LLM: Qwen-Max → DeepSeek  │
│  Grafana · Loki 日志收集     │    │  (DashScope, 跨境数据受控)    │
└──────────────────────────┘    └──────────────────────────┘
```

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · Celery 5 · Pydantic v2 · structlog · slowapi |
| 数据库 | PostgreSQL 15 · Redis 7（AOF+RDB）· Alembic |
| ML | LightGBM · IsolationForest · SHAP · scikit-learn · SDV（数据合成）· 特征元数据契约 |
| 前端 | Vue 3.5 + TS · Vite 5 · Pinia · ECharts 5 · vue-router · axios |
| AI | DashScope Qwen-Max（OpenAI 兼容）+ DeepSeek 回退 + 规则模板兜底 |
| DevOps | Docker Compose（11 服务）· nginx（SSE 反代）· Prometheus / Grafana / Loki / Promtail · Makefile |

## 快速开始

```bash
# 1. 构建前端产物（nginx 容器直接挂载 ./frontend/dist 托管）
cd frontend && npm install && npm run build && cd ..

# 2. 启动全部服务（migrate one-shot 服务会先执行 alembic upgrade head，
#    api/worker/beat 等 schema 就绪后才启动）
docker compose up -d --build      # migrate / api / worker / beat / postgres / redis / nginx / prometheus / grafana / loki / promtail
# API 文档: http://localhost:8000/docs （仅本机回环可访问，统一经 nginx 入口）
# 前端:     http://localhost:80   (nginx 托管 dist)
# Grafana:  http://localhost:3000
```

非 Compose 手动部署时，数据库初始化按以下顺序执行：

```bash
cd backend
alembic upgrade head                                   # 1. 数据库迁移建表
python -m scripts.smoke_seed                           # 2. 种子数据（演示账号/员工，输出 TOTP_SECRET）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000   # 3. 启动 API
```

后端单独开发：

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[ml,dev]"
cp .env.example .env
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

一键重训模型（7 步流水线：数据 → 特征 → 结构化 → 行为 → 融合 → SHAP → 公平性，退出码即结果）：

```bash
.venv\Scripts\python.exe -m app.ml.train_pipeline
```

## 测试与质量

**288+ 测试用例**（12 个测试文件，覆盖真实应用注入）：

| 领域 | 用例数 | 覆盖内容 |
|---|---|---|
| 覆盖率补强 | 68 | 边缘分支 / 异常路径 |
| 服务层 | 43 | 风控、预警、审计、LLM 服务 |
| API 端点 | 33 | 认证、员工、风险、管理端 |
| ML 模块 | 33 | 特征工程、融合、SHAP、公平性 |
| 风控服务 | 29 | 预测链路、缓存降级 |
| 治理端点 | 21 | Kill Switch、漂移、公平性 API |
| 租户隔离 | 19 | 行级隔离、WebSocket 分区 |
| 预警状态机 | 16 | 全状态流转 + 升级路径 |
| 数据保留 | 13 | PIPL 清理策略 |
| 特征契约 | 9 | 训练 / 推理特征一致性 |

## 目录结构

```
backend/    FastAPI（api / services / ml / tasks / core / models / kb / alembic / tests）
frontend/   Vue 3 前端（9 视图 + 路由守卫 + 401 静默刷新队列）
docs/       项目文档体系（D01-D11）
monitoring/ Prometheus / Promtail 配置
docker-compose.yml  一键编排 11 服务
```

## RAG 制度知识库问答（feat/rag-kb）

面向 HR 场景的企业制度知识库智能问答：文档上传 → 解析切分 → 向量化入库 → 混合检索 → 流式生成，答案附**引用溯源**，无依据自动**拒答**。

### 架构与关键设计

| 环节 | 方案 |
|---|---|
| 文档解析 | PDF（pypdf）/ DOCX（python-docx，Heading 层级感知）/ Markdown；表格块原子保留不切碎 |
| Chunking | 标题感知递归切分，512 token / 重叠 64，heading_path 随切片携带 |
| 检索 | **混合召回**：jieba 预分词 + tsvector GIN 词法路 + pgvector HNSW 语义路 → **RRF(k=60) 融合** |
| 重排 | DashScope gte-rerank（feature flag + 800ms 硬超时，失败回退 RRF 排序——增益项而非依赖项） |
| 编排 | **LangGraph 状态机**：retrieve → rerank → generate → self_check（引用校验失败回环重生成一次）；流式/非流式共用同一批节点函数，避免双实现漂移 |
| 幻觉防御 | 四层：低置信度拒答（不调 LLM）→ 强制 [n] 引用约束 → 检索内容标记"数据非指令"防提示词注入 → self_check 引用完整性校验 |
| LLM 复用 | Qwen-Max → DeepSeek-V3 → 拒答话术三级回退，SSE 流式输出 |
| 合规 | 多租户行级隔离（chunks 冗余 tenant_id）+ 入库 PII 扫描脱敏 + 查询审计哈希链（问题仅记 SHA256）+ 文件 Redis 中转不落盘 |

### 快速启用

```bash
# 1. compose 已切换 pgvector/pgvector:pg15 镜像
docker compose up -d postgres redis
# 2. 安装 rag 依赖组并执行迁移（0003 自动 CREATE EXTENSION vector）
pip install ".[serving,rag]"
alembic upgrade head
# 3. .env 开启 RAG_ENABLED=true 后重启 api / worker
```

前端入口：左侧导航「知识库」——左栏文档管理（上传/进度轮询/删除），右栏制度问答（打字机流式 + 引用卡片）。

### 检索质量评估

```bash
python scripts/eval_rag.py --tenant-id <UUID>   # 50 题黄金集（40 可答 + 10 应拒答）
```

产出 `scripts/eval_report.json`：Recall@5 / MRR / 拒答准确率 / 检索阶段 P50-P95 延迟。

## 文档体系

见 [docs/README.md](docs/README.md) — 完整 D01-D11 软件工程文档（立项 / 需求 / 架构 / 数据库 / API / 手册 / 测试 / 进度 / 风险 / 部署 / 验收）。

## 联系方式

- 作者：邝振华 · GitHub：[kajykk](https://github.com/kajykk)
- 邮箱：1754902912@qq.com
