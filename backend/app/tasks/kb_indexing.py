"""RAG 索引任务（feat/rag-kb）：文档解析 → PII 脱敏 → 切分 → 嵌入 → 落库.

任务由 API 上传接口入队（index_kb_document），Worker 进程执行。
降级策略：
  - Redis 文件缓存缺失 → 标记 failed（cache-miss，提示重新上传）
  - 任何解析/嵌入异常 → 标记 failed 并记录原因
"""
from __future__ import annotations

import asyncio

from app.celery_app import celery_app
from app.core.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(
    name="app.tasks.kb_indexing.index_kb_document",
    bind=True,
    max_retries=0,  # 索引失败不自动重试（嵌入有内部重试；重试易放大成本）
    acks_late=True,
)
def index_kb_document(self, document_id: str) -> dict:
    """索引单文档（同步入口包装异步实现）."""
    try:
        from app.kb.service import finalize_index

        result = asyncio.run(finalize_index(document_id))
        logger.info("索引任务完成 | doc=%s result=%s", document_id, result)
        return {"document_id": document_id, **result}
    except Exception as e:  # noqa: BLE001 - 任务级兜底：落库 failed 而非静默丢失
        logger.error("索引任务异常 | doc=%s err=%s", document_id, e)
        try:
            from app.kb.service import mark_index_failed

            asyncio.run(mark_index_failed(document_id, f"索引异常：{e}"))
        except Exception:  # noqa: BLE001 - 二次失败仅留日志
            pass
        return {"document_id": document_id, "status": "failed", "reason": str(e)}
