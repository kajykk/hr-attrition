"""RAG 知识库服务门面：入库 / 删除 / 同步问答 / 流式问答（feat/rag-kb）.

进程边界说明：
  - API 进程：校验 → 建文档记录 → 原始字节入 Redis（rag:file:{doc_id}，TTL 1h）→ 入队任务
  - Worker 进程（Celery）：从 Redis 取回字节 → 解析 → PII 脱敏 → 切分 → 嵌入 → 落库
  - 文件不落盘（Redis 中转，最小化存储）；Redis 不可用时上传直接失败并提示

所有公开方法在 RAG 未启用时抛 RagDisabledError，由 API 层转换为 503。
"""
import hashlib
import re
import time
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

FILE_CACHE_TTL_SECONDS = 3600


class RagDisabledError(Exception):
    """RAG 功能未启用."""


def ensure_rag_enabled() -> None:
    """前置校验：开关 + 数据库类型 + rag 依赖组."""
    if not settings.RAG_ENABLED:
        raise RagDisabledError("RAG 功能未开启（RAG_ENABLED=false）")
    if not settings.DATABASE_URL.startswith(("postgresql", "postgres")):
        raise RagDisabledError("RAG 需要 PostgreSQL 数据库")
    try:
        import jieba  # noqa: F401
        import pgvector  # noqa: F401
    except ImportError as e:
        raise RagDisabledError(f"缺少 .[rag] 依赖组：{e}") from e


# ===== PII 扫描（入库侧，对应简历 "入库 PII 扫描"）=====
_PII_PATTERNS = [
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("bank_card", re.compile(r"(?<!\d)[3-6]\d{15,18}(?!\d)")),
]
_PII_MASK = "***PII***"


def scan_and_mask_pii(text: str) -> tuple[str, int]:
    """检测并脱敏 PII。返回 (脱敏后文本, 命中次数)。"""
    hits = 0
    for _, pattern in _PII_PATTERNS:
        text, n = pattern.subn(_PII_MASK, text)
        hits += n
    return text, hits


# ===== 上传与索引 =====
@dataclass
class UploadResult:
    document_id: str
    task_id: str | None = None
    deduplicated: bool = False


async def upload_document(
    session: AsyncSession,
    tenant_id: str,
    uploaded_by: str,
    filename: str,
    data: bytes,
) -> UploadResult:
    """上传入口：快速校验 + 去重 + 建 processing 记录 + 字节入 Redis + 入队."""
    from fastapi import HTTPException

    from app.kb.parsers import MAX_FILE_SIZE_BYTES, SUPPORTED_EXTENSIONS

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型 {ext}（支持 {sorted(SUPPORTED_EXTENSIONS)}）")
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 20MB 上限")

    file_hash = hashlib.sha256(data).hexdigest()
    KBDocumentModel = _kb_document_model()
    existing = (
        await session.execute(
            select(KBDocumentModel.id).where(
                KBDocumentModel.tenant_id == UUID(tenant_id),
                KBDocumentModel.file_hash == file_hash,
            )
        )
    ).first()
    if existing is not None:
        return UploadResult(document_id=str(existing[0]), deduplicated=True)

    # Redis 暂存原始字节（Worker 取用；不落盘）
    doc = KBDocumentModel(
        tenant_id=UUID(tenant_id),
        title=_clean_title(filename),
        file_type=ext.lstrip("."),
        file_hash=file_hash,
        status="processing",
        uploaded_by=UUID(uploaded_by),
    )
    session.add(doc)
    await session.flush()
    doc_id = str(doc.id)

    redis = await _get_redis_or_fail()
    try:
        await redis.set(f"rag:file:{doc_id}", data, ex=FILE_CACHE_TTL_SECONDS)
    except Exception as e:
        raise HTTPException(status_code=503, detail="存储队列不可用，请稍后重试") from e

    task_id = None
    try:
        from app.tasks.kb_indexing import index_kb_document

        task_id = index_kb_document.delay(doc_id).id
    except Exception as e:  # noqa: BLE001 - broker 故障：标记失败而非阻塞响应
        logger.error("索引入队失败 | doc=%s err=%s", doc_id, e)
        doc.status = "failed"
        doc.error_message = "任务队列不可用，请重新上传"

    logger.info("文档已接收 | doc=%s file=%s size=%d tenant=%s", doc_id, filename, len(data), tenant_id)
    return UploadResult(document_id=doc_id, task_id=task_id)


async def finalize_index(document_id: str) -> dict:
    """执行索引（Celery 任务调用）：取回字节 → 解析 → 脱敏 → 切分 → 嵌入 → 落库.

    幂等：重复执行先清空旧切片再写入。
    """
    from app.kb.chunker import split_document
    from app.kb.embedding_client import embed_texts
    from app.kb.parsers import ParseError, RawSection, parse_document
    from app.models.kb import KBChunk, KBDocument

    doc_id = UUID(document_id)
    async with _session_factory()() as session:
        doc = (
            await session.execute(select(KBDocument).where(KBDocument.id == doc_id))
        ).scalar_one_or_none()
        if doc is None:
            logger.error("索引目标文档不存在 | doc=%s", document_id)
            return {"status": "missing"}

        raw = None
        redis = await _get_redis_soft()
        if redis is not None:
            try:
                raw = await redis.get(f"rag:file:{document_id}")
            except Exception as e:  # noqa: BLE001
                logger.warning("Redis 读取文件缓存失败 | err=%s", e)

        if not raw:
            doc.status = "failed"
            doc.error_message = "文件缓存过期，请重新上传"
            await session.commit()
            return {"status": "failed", "reason": "cache-miss"}

        try:
            filename = f"doc.{doc.file_type}"
            sections = parse_document(filename, raw)
        except ParseError as e:
            doc.status = "failed"
            doc.error_message = str(e)[:500]
            await session.commit()
            return {"status": "failed", "reason": str(e)}

        total_pii = 0
        prepared: list[RawSection] = []
        for section in sections:
            masked, hits = scan_and_mask_pii(section.text)
            total_pii += hits
            prepared.append(RawSection(heading_path=section.heading_path, text=masked))

        chunks = split_document(prepared)
        vectors = await embed_texts([c.content for c in chunks])

        # 幂等清理旧切片
        await session.execute(delete(KBChunk).where(KBChunk.document_id == doc_id))

        # Core 多行插入：tsv 由数据库端 to_tsvector('simple', 预分词文本) 计算
        if chunks:
            rows = []
            for chunk, vec in zip(chunks, vectors):
                rows.append(
                    {
                        "document_id": doc_id,
                        "tenant_id": doc.tenant_id,
                        "seq": chunk.seq,
                        "content": chunk.content,
                        "heading_path": chunk.heading_path[:500],
                        "token_count": chunk.token_count,
                        "embedding": vec,
                        "tsv": func.to_tsvector("simple", " ".join(_tokenize(chunk.content))),
                    }
                )
            await session.execute(pg_insert(KBChunk).values(rows))

        doc.status = "ready"
        doc.chunk_count = len(chunks)
        doc.pii_hits = total_pii
        await session.commit()

    logger.info("文档索引完成 | doc=%s chunks=%d pii=%d", document_id, len(chunks), total_pii)
    return {"status": "ready", "chunks": len(chunks), "pii_hits": total_pii}


async def mark_index_failed(document_id: str, reason: str) -> None:
    """任务异常兜底：落库 failed 状态."""
    from app.models.kb import KBDocument

    try:
        async with _session_factory()() as session:
            doc = (
                await session.execute(
                    select(KBDocument).where(KBDocument.id == UUID(document_id))
                )
            ).scalar_one_or_none()
            if doc is not None:
                doc.status = "failed"
                doc.error_message = reason[:500]
                await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("标记失败状态异常 | doc=%s err=%s", document_id, e)


async def get_document_status(session: AsyncSession, tenant_id: str, document_id: str):
    """查询本租户文档状态（进度轮询通道）."""
    from app.models.kb import KBDocument

    result = await session.execute(
        select(KBDocument).where(
            KBDocument.id == UUID(document_id),
            KBDocument.tenant_id == UUID(tenant_id),
        )
    )
    return result.scalar_one_or_none()


async def list_documents(session: AsyncSession, tenant_id: str) -> list:
    from app.models.kb import KBDocument

    result = await session.execute(
        select(KBDocument)
        .where(KBDocument.tenant_id == UUID(tenant_id))
        .order_by(KBDocument.created_at.desc())
        .limit(100)
    )
    return list(result.scalars())


async def delete_document(session: AsyncSession, tenant_id: str, document_id: str) -> bool:
    """删除文档（chunks 经 FK 级联），仅限本租户。返回是否删除."""
    from app.models.kb import KBDocument

    result = await session.execute(
        delete(KBDocument).where(
            KBDocument.id == UUID(document_id),
            KBDocument.tenant_id == UUID(tenant_id),
        )
    )
    return (result.rowcount or 0) > 0


# ===== 问答 =====
@dataclass
class AnswerResult:
    answer: str
    citations: list[dict] = field(default_factory=list)
    refused: bool = False
    latency_ms: int = 0


async def query(tenant_id: str, question: str) -> AnswerResult:
    """同步问答：LangGraph graph.ainvoke 驱动共享节点."""
    started = _now_ms()
    graph = _require_graph()
    final_state = await graph.ainvoke({"question": question, "tenant_id": tenant_id})
    citations = _build_citations(final_state.get("chunks", []), final_state.get("citations", []))
    return AnswerResult(
        answer=final_state.get("answer", ""),
        citations=citations,
        refused=bool(final_state.get("refused")),
        latency_ms=_now_ms() - started,
    )


async def query_stream(tenant_id: str, question: str):
    """流式问答：按图顺序驱动共享节点函数，token 实时外送.

    yields：
      {"type": "token", "text": ...}
      {"type": "done", "answer": ..., "citations": [...], "refused": bool}
    """
    from app.kb.graph import run_stream_pipeline

    async for event in run_stream_pipeline({"question": question, "tenant_id": tenant_id}):
        yield event


# ---------------------------------------------------------------- 内部工具
def _kb_document_model():
    from app.models.kb import KBDocument

    return KBDocument


def _session_factory():
    from app.db.session import async_session_factory

    return async_session_factory


async def _get_redis_soft():
    try:
        from app.core.redis import get_redis

        return get_redis()
    except Exception:  # noqa: BLE001
        return None


async def _get_redis_or_fail():
    redis = await _get_redis_soft()
    if redis is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="存储队列不可用，请稍后重试")
    return redis


def _require_graph():
    try:
        from app.kb.graph import build_graph

        return build_graph()
    except RuntimeError as e:
        raise RagDisabledError(str(e)) from e


def _build_citations(chunks: list, cited_ids: list[int]) -> list[dict]:
    by_index = {i + 1: c for i, c in enumerate(chunks)}
    out, seen = [], set()
    for idx in sorted(cited_ids):
        chunk = by_index.get(idx)
        if chunk is not None and idx not in seen:
            seen.add(idx)
            out.append(
                {
                    "index": idx,
                    "title": chunk.document_title,
                    "heading_path": getattr(chunk, "heading_path", "") or "",
                    "snippet": chunk.content[:120],
                }
            )
    return out


def _clean_title(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return stem.strip()[:200] or "未命名文档"


def _tokenize(text: str) -> list[str]:
    from app.kb.tokenizer_zh import tokenize

    return tokenize(text)


def _now_ms() -> int:
    return int(time.perf_counter() * 1000)
