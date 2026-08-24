"""RAG 知识库 API：文档管理 + 问答（feat/rag-kb）.

权限：
  - 上传/删除/列表/状态：admin / hr_manager（制度库属管理资产）
  - 问答（同步/流式）：所有登录用户

未启用 RAG（开关关闭/依赖缺失/非 PostgreSQL）时全部返回 503。
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.user import User

logger = get_logger(__name__)

router = APIRouter()

UPLOAD_ROLES = ("admin", "hr_manager")
MANAGE_ROLES = ("admin", "hr_manager")


def _ensure_enabled() -> None:
    from app.kb.service import RagDisabledError, ensure_rag_enabled

    try:
        ensure_rag_enabled()
    except RagDisabledError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e


class KbQueryRequest(BaseModel):
    """问答请求."""

    question: str = Field(min_length=2, max_length=500, description="用户问题")


def _doc_to_dict(doc) -> dict:
    return {
        "id": str(doc.id),
        "title": doc.title,
        "file_type": doc.file_type,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "pii_hits": doc.pii_hits,
        "error_message": doc.error_message,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


@router.post("/documents", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    user: User = Depends(require_role(*UPLOAD_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """上传制度文档并触发异步索引."""
    _ensure_enabled()
    from app.kb.service import upload_document as svc_upload

    data = await file.read()
    result = await svc_upload(db, str(user.tenant_id), str(user.id), file.filename or "", data)
    return {
        "document_id": result.document_id,
        "task_id": result.task_id,
        "deduplicated": result.deduplicated,
    }


@router.get("/documents")
async def list_documents(
    user: User = Depends(require_role(*MANAGE_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """本租户文档列表（最近 100 条）."""
    _ensure_enabled()
    from app.kb.service import list_documents as svc_list

    docs = await svc_list(db, str(user.tenant_id))
    return {"documents": [_doc_to_dict(d) for d in docs]}


@router.get("/documents/{document_id}")
async def document_status(
    document_id: str,
    user: User = Depends(require_role(*MANAGE_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """单文档状态（索引进度轮询通道）."""
    _ensure_enabled()
    from app.kb.service import get_document_status

    try:
        doc = await get_document_status(db, str(user.tenant_id), document_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="非法文档 ID") from e
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return _doc_to_dict(doc)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    user: User = Depends(require_role(*MANAGE_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除文档及其切片（级联）."""
    _ensure_enabled()
    from app.kb.service import delete_document as svc_delete

    try:
        deleted = await svc_delete(db, str(user.tenant_id), document_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="非法文档 ID") from e
    if not deleted:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"deleted": True}


@router.post("/query")
async def kb_query(body: KbQueryRequest, user: User = Depends(get_current_user)) -> dict:
    """同步知识库问答（带引用溯源与拒答语义）."""
    _ensure_enabled()
    from app.kb.service import query as svc_query

    result = await svc_query(str(user.tenant_id), body.question.strip())
    await _audit_kb(str(user.tenant_id), str(user.id), body.question, result.refused)
    return {
        "answer": result.answer,
        "citations": result.citations,
        "refused": result.refused,
        "latency_ms": result.latency_ms,
    }


@router.post("/query/stream")
async def kb_query_stream(body: KbQueryRequest, user: User = Depends(get_current_user)):
    """流式知识库问答（SSE：event: token / event: done）."""
    _ensure_enabled()
    from app.kb.service import query_stream as svc_stream

    async def sse_gen():
        try:
            async for event in svc_stream(str(user.tenant_id), body.question.strip()):
                name = "token" if event["type"] == "token" else "done"
                payload = (
                    {"text": event["text"]}
                    if name == "token"
                    else {
                        "answer": event["answer"],
                        "citations": event["citations"],
                        "refused": event["refused"],
                    }
                )
                yield f"event: {name}\ndata: {_json_dumps(payload)}\n\n"
        except Exception as e:  # noqa: BLE001 - SSE 中途异常以 error 帧收尾
            logger.error("KB 流式问答异常 | err=%s", e)
            yield f"event: error\ndata: {_json_dumps({'detail': '生成中断，请重试'})}\n\n"

    return StreamingResponse(sse_gen(), media_type="text/event-stream")


# ---------------------------------------------------------------- 内部工具
async def _audit_kb(tenant_id: str, user_id: str, question: str, refused: bool) -> None:
    """审计知识库查询：走 audit_service 哈希链；问题明文不入日志，仅记 SHA256."""
    import hashlib
    from uuid import UUID

    try:
        from app.db.session import async_session_factory
        from app.services.audit_service import append_audit_log

        q_hash = hashlib.sha256(question.encode()).hexdigest()
        async with async_session_factory() as session:
            await append_audit_log(
                session,
                tenant_id=UUID(tenant_id),
                user_id=UUID(user_id),
                action="kb.query",
                resource_type="kb_document",
                after_value={"question_sha256": q_hash[:16], "refused": refused},
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001 - 审计失败不影响主流程
        logger.warning("KB 审计写入失败 | err=%s", e)


def _json_dumps(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)
