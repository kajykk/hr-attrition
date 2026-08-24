"""RAG 知识库问答子包（feat/rag-kb）.

模块职责：
  - parsers.py        文档解析（PDF/DOCX/MD/TXT → RawSection）
  - chunker.py        标题感知切分（512 token / 重叠 64）
  - tokenizer_zh.py   jieba 分词 + tsvector/tsquery 构造
  - embedding_client  DashScope text-embedding-v3 批量嵌入（Redis 缓存 + hash 兜底）
  - rerank_client.py  DashScope gte-rerank 重排（可开关，超时降级跳过）
  - retriever.py      混合检索：pgvector HNSW 语义 + tsvector 词法 → RRF 融合
  - prompts.py        System Prompt 与拒答话术（注入隔离声明）
  - graph.py          LangGraph 状态机编排 + 节点实现（节点函数为唯一逻辑源）
  - service.py        KnowledgeBaseService 门面（入库/删除/问答/流式问答）

启用前提：
  1. PostgreSQL 且已安装 pgvector 扩展（迁移 0003 自动 CREATE EXTENSION）
  2. `pip install ".[rag]"`
  3. 环境变量 RAG_ENABLED=true
"""
