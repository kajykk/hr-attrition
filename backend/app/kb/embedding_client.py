"""Embedding 客户端：DashScope text-embedding-v3 批量嵌入（feat/rag-kb）.

特性：
  - 批量调用（每请求 ≤10 条，DashScope 限制），指数退避重试
  - Redis 缓存（key = sha256(model + text)，TTL 30 天）——重复入库零成本
  - provider=hash：离线开发/测试用的确定性特征哈希嵌入（生产禁用）
"""
import asyncio
import hashlib
import math
import struct

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.kb.tokenizer_zh import tokenize

logger = get_logger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
BATCH_SIZE = 10
CACHE_TTL_SECONDS = 30 * 24 * 3600


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量嵌入入口：按 provider 分发."""
    if not texts:
        return []
    provider = settings.RAG_EMBEDDING_PROVIDER
    if provider == "hash":
        return [_hash_embed(t) for t in texts]
    return await _dashscope_embed(texts)


async def _embed_batch_with_cache(texts: list[str]) -> list[list[float]]:
    """带 Redis 缓存的批量嵌入."""
    vectors: list[list[float] | None] = [None] * len(texts)
    missing: list[int] = []

    redis = None
    try:
        from app.core.redis import get_redis

        redis = get_redis()
    except Exception:  # noqa: BLE001
        logger.debug("Redis 不可用，embedding 缓存降级为直连")

    # 1) 命中缓存
    keys = [
        "rag:emb:" + hashlib.sha256(f"{settings.RAG_EMBEDDING_MODEL}:{t}".encode()).hexdigest()
        for t in texts
    ]
    if redis is not None:
        try:
            cached = await asyncio.gather(*(redis.get(k) for k in keys))
            for i, raw in enumerate(cached):
                if raw:
                    floats = struct.unpack(f"{len(raw) // 4}f", raw)
                    vectors[i] = list(floats)
        except Exception as e:  # noqa: BLE001
            logger.warning("embedding 缓存读取失败，直连模式 | err=%s", e)

    missing = [i for i, v in enumerate(vectors) if v is None]
    if not missing:
        return [v for v in vectors if v is not None]

    # 2) 未命中的分批远程调用
    results: dict[int, list[float]] = {}
    for start in range(0, len(missing), BATCH_SIZE):
        idx_group = missing[start : start + BATCH_SIZE]
        group_vecs = await _call_dashscope_with_retry([texts[i] for i in idx_group])
        for i, vec in zip(idx_group, group_vecs):
            results[i] = vec

    # 3) 回填缓存
    if redis is not None and results:
        try:
            pipe = redis.pipeline()
            for i, vec in results.items():
                pipe.set(keys[i], struct.pack(f"{len(vec)}f", *vec), ex=CACHE_TTL_SECONDS)
            await asyncio.gather(pipe.execute(), return_exceptions=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("embedding 缓存写入失败 | err=%s", e)

    final: list[list[float]] = []
    for i in range(len(texts)):
        if vectors[i] is not None:
            final.append(vectors[i])  # type: ignore[arg-type]
        else:
            final.append(results[i])
    return final


def _api_key_usable() -> bool:
    """密钥可用性校验（空值/占位值/过短一律视为未配置）."""
    key = settings.DASHSCOPE_API_KEY
    return bool(key) and len(key) >= 20 and "change" not in key.lower()


async def _call_dashscope_with_retry(texts: list[str], max_retries: int = 3):
    """DashScope /embeddings 批量调用，429/5xx 指数退避."""
    if not _api_key_usable():
        raise RuntimeError("DASHSCOPE_API_KEY 未配置或为占位值")
    headers = {
        "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": settings.RAG_EMBEDDING_MODEL, "input": texts}
    delay = 0.5
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(max_retries):
            try:
                resp = await client.post(
                    f"{DASHSCOPE_BASE_URL}/embeddings", headers=headers, json=payload
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"upstream {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                data = resp.json().get("data", [])
                data.sort(key=lambda d: d.get("index", 0))
                return [d["embedding"] for d in data]
            except (httpx.HTTPError,) as e:
                last_err = e
                logger.warning(
                    "embedding 调用失败(第 %d 次)，%.1fs 后重试 | err=%s", attempt + 1, delay, e
                )
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError(f"embedding 连续 {max_retries} 次失败") from last_err


def _hash_embed(text: str) -> list[float]:
    """确定性特征哈希嵌入（仅离线开发/测试）.

    jieba 词元 → blake2b 映射到 [0, dim) 桶位并带符号累加，L2 归一化。
    相似文本有重叠词元 → 余弦相似度非零，可支撑本地 demo 与单测。
    """
    dim = settings.RAG_EMBEDDING_DIM
    vec = [0.0] * dim
    for tok in tokenize(text):
        digest = hashlib.blake2b(tok.encode(), digest_size=8).digest()
        (idx,) = struct.unpack("<Q", digest)
        sign = 1.0 if digest[7] & 1 else -1.0
        vec[idx % dim] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


async def _dashscope_embed(texts: list[str]) -> list[list[float]]:
    """生产路径：key 缺失/占位时非生产环境自动降级 hash，生产环境直接报错."""
    if not _api_key_usable():
        if settings.is_prod:
            raise RuntimeError("生产环境必须配置有效的 DASHSCOPE_API_KEY")
        logger.warning("DASHSCOPE_API_KEY 未配置或为占位值，RAG embedding 自动降级 hash 提供者（仅限开发/测试）")
        return [_hash_embed(t) for t in texts]
    return await _embed_batch_with_cache(texts)
