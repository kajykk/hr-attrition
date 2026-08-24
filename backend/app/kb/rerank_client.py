"""重排客户端：DashScope gte-rerank（feat/rag-kb）.

设计：
  - 独立 feature flag（RAG_RERANK_ENABLED），关闭即跳过
  - 硬超时（RAG_RERANK_TIMEOUT_MS，默认 800ms）：超时/异常一律返回 None，
    调用方回退 RRF 排序——重排是"增益项"而非"依赖项"
"""
import asyncio

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# DashScope 原生服务端点（非 OpenAI 兼容协议）
RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"


def _api_key_usable() -> bool:
    """密钥可用性校验（与 embedding_client 同口径：空值/占位值/过短视为未配置）."""
    key = settings.DASHSCOPE_API_KEY
    return bool(key) and len(key) >= 20 and "change" not in key.lower()


async def rerank(query: str, documents: list[str], top_n: int) -> list[tuple[int, float]] | None:
    """对候选文档重排.

    返回 [(原索引, 相关性分数)] 按分数降序取 top_n；任何异常返回 None（调用方回退）。
    """
    if not settings.RAG_RERANK_ENABLED or not documents:
        return None
    if not _api_key_usable():
        logger.debug("DASHSCOPE_API_KEY 未配置或为占位值，跳过重排")
        return None

    timeout_ms = settings.RAG_RERANK_TIMEOUT_MS / 1000
    try:
        return await asyncio.wait_for(
            _call_rerank(query, documents, top_n), timeout=timeout_ms
        )
    except (asyncio.TimeoutError, httpx.HTTPError, RuntimeError, KeyError, ValueError) as e:
        logger.warning("重排失败，回退 RRF 排序 | err=%s", e)
        return None


async def _call_rerank(query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    headers = {
        "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.RAG_RERANK_MODEL,
        "input": {"query": query, "documents": documents},
        "parameters": {"return_documents": False, "top_n": top_n},
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(RERANK_URL, headers=headers, json=payload)
        resp.raise_for_status()
        results = resp.json()["output"]["results"]
        return [(int(r["index"]), float(r["relevance_score"])) for r in results]
