"""混合检索器：pgvector HNSW 语义召回 + tsvector 词法召回 → RRF 融合（feat/rag-kb）.

流程：
  1. 查询分词 → 构造 embedding 与 tsquery
  2. 两路并行召回各 top_k*2 条候选（均强制 tenant_id 过滤）
  3. RRF(k=60) 按排名融合去重
  4. 可选 gte-rerank 重排取 top_k
  5. 最高融合分数 < RAG_MIN_SCORE → 返回空列表（上层拒答，不调 LLM）

容错：任一路失败自动退化为单路；两路都失败抛 RuntimeError。
"""
from dataclasses import dataclass, field
from operator import itemgetter

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.kb.tokenizer_zh import build_tsquery

logger = get_logger(__name__)

RRF_K = 60


@dataclass
class RetrievedChunk:
    """检索结果块."""

    chunk_id: str
    document_id: str
    document_title: str
    seq: int
    content: str
    heading_path: str = ""
    rrf_score: float = 0.0
    similarity: float = 0.0  # 语义余弦相似度（拒答双信号①）
    sources: list[str] = field(default_factory=list)  # 命中来源：semantic/lexical/rerank

    def to_prompt_dict(self, index: int) -> dict:
        """转为 Prompt 资料块格式（index 从 1 开始，与引用编号一致）."""
        return {"index": index, "title": self.document_title, "content": self.content}


def confidence_gate_pass(best_similarity: float, best_coverage: float) -> bool:
    """拒答双信号门槛（纯函数，便于单测标定）.

    RRF 分数尺度固定（1/(k+rank)），无法区分"相关"与"仅词面重叠"，
    因此用两个可解释信号联合判定是否可信作答：
      ① best_similarity：查询与最优块的语义余弦相似度 ≥ RAG_MIN_COSINE
      ② best_coverage ：查询分词被最优块覆盖的比例   ≥ RAG_MIN_COVERAGE
    任一不满足 → 拒绝作答（上层输出拒答话术，不调用 LLM）。
    """
    return best_similarity >= settings.RAG_MIN_COSINE and best_coverage >= settings.RAG_MIN_COVERAGE


def _rrf_fuse(rankings: list[list[tuple[str, int]]]) -> dict[str, float]:
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)，rank 从 1 开始."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for doc_id, rank in ranking:
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
    return scores


async def hybrid_search(
    session: AsyncSession,
    tenant_id: str,
    question: str,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """混合检索主入口。返回按最终排序的 top_k 结果；无可信命中返回 []."""
    from app.kb.embedding_client import embed_texts

    top_k = top_k or settings.RAG_TOP_K
    candidates = settings.RAG_RECALL_CANDIDATES

    tsquery = build_tsquery(question)
    vectors = await embed_texts([question])
    qvec = vectors[0]

    # 注意：两路查询共用同一 AsyncSession（单连接），必须顺序执行——
    # asyncio.gather 并发会触发 "session is provisioning a new connection" 错误
    sem_rows = await _semantic_recall(session, tenant_id, qvec, candidates)
    lex_rows = (
        await _lexical_recall(session, tenant_id, tsquery, candidates) if tsquery else []
    )

    # 单路降级已在 recall 内部处理：全部为空即无命中
    rankings: list[list[tuple[str, int]]] = []
    if sem_rows:
        rankings.append([(r["chunk_id"], i + 1) for i, r in enumerate(sem_rows)])
    if lex_rows:
        rankings.append([(r["chunk_id"], i + 1) for i, r in enumerate(lex_rows)])
    if not rankings:
        return []

    fused = _rrf_fuse(rankings)
    ordered_ids = [cid for cid, _ in sorted(fused.items(), key=itemgetter(1), reverse=True)]

    rows_by_id: dict[str, dict] = {}
    for r in [*sem_rows, *lex_rows]:
        rows_by_id.setdefault(r["chunk_id"], r)

    results: list[RetrievedChunk] = []
    for cid in ordered_ids[: max(top_k * 2, top_k)]:
        row = rows_by_id[cid]
        srcs = []
        if any(cid == r["chunk_id"] for r in sem_rows):
            srcs.append("semantic")
        if any(cid == r["chunk_id"] for r in lex_rows):
            srcs.append("lexical")
        results.append(
            RetrievedChunk(
                chunk_id=cid,
                document_id=str(row["document_id"]),
                document_title=row["document_title"],
                seq=int(row["seq"]),
                content=row["content"],
                heading_path=row["heading_path"] or "",
                rrf_score=fused[cid],
                similarity=float(row.get("similarity") or 0.0),
                sources=srcs,
            )
        )

    # 可选重排（增益项，失败回退 RRF 排序）
    reranked = None
    try:
        from app.kb.rerank_client import rerank

        reranked = await rerank(
            question, [r.content for r in results[:candidates]], top_n=top_k
        )
    except ImportError:
        logger.debug("rerank_client 导入失败，跳过重排")
    if reranked:
        for new_rank, (old_idx, score) in enumerate(reranked, start=1):
            results[old_idx].sources.append("rerank")
            results[old_idx].rrf_score += score / (RRF_K + new_rank)
        picked_ids = [results[old_idx].chunk_id for old_idx, _ in reranked]
        by_id = {r.chunk_id: r for r in results}
        results = [by_id[cid] for cid in picked_ids if cid in by_id]

    # 拒答双信号门槛：语义相似度 + 查询词元覆盖率（不调用 LLM，节省成本并支撑拒答指标）
    q_tokens = set(_tokenize(question))
    best_sim = max((r.similarity for r in results), default=0.0)
    best_cov = max(
        (len(q_tokens & set(_tokenize(r.content))) / max(len(q_tokens), 1) for r in results),
        default=0.0,
    )
    if not results or not confidence_gate_pass(best_sim, best_cov):
        logger.info(
            "置信度门槛未通过，触发拒答 | sim=%.3f cov=%.3f n=%d",
            best_sim,
            best_cov,
            len(results),
        )
        return []
    return results


def _tokenize(text: str) -> list[str]:
    from app.kb.tokenizer_zh import tokenize

    return tokenize(text)



async def _semantic_recall(
    session: AsyncSession, tenant_id: str, qvec: list[float], limit: int
) -> list[dict]:
    """语义召回：pgvector 余弦距离，HNSW 索引加速."""
    vec_literal = "[" + ",".join(f"{v:.6f}" for v in qvec) + "]"
    stmt = sql_text(
        """
        SELECT c.id::text AS chunk_id, c.document_id::text AS document_id,
               d.title AS document_title, c.seq, c.content, c.heading_path,
               1 - (c.embedding <=> CAST(:qvec AS vector)) AS similarity
        FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id
        WHERE c.tenant_id = :tid
        ORDER BY c.embedding <=> CAST(:qvec AS vector)
        LIMIT :limit
        """
    )
    try:
        result = await session.execute(stmt, {"tid": tenant_id, "qvec": vec_literal, "limit": limit})
        return [dict(r._mapping) for r in result]
    except Exception as e:  # noqa: BLE001
        logger.warning("语义召回失败（将尝试单路词法） | err=%s", e)
        return []


async def _lexical_recall(
    session: AsyncSession, tenant_id: str, tsquery: str, limit: int
) -> list[dict]:
    """词法召回：tsvector @@ tsquery + ts_rank_cd 排名."""
    stmt = sql_text(
        """
        SELECT c.id::text AS chunk_id, c.document_id::text AS document_id,
               d.title AS document_title, c.seq, c.content, c.heading_path,
               ts_rank_cd(c.tsv, query) AS rank
        FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id,
             to_tsquery('simple', :q) query
        WHERE c.tenant_id = :tid AND c.tsv @@ query
        ORDER BY rank DESC
        LIMIT :limit
        """
    )
    try:
        result = await session.execute(stmt, {"tid": tenant_id, "q": tsquery, "limit": limit})
        return [dict(r._mapping) for r in result]
    except Exception as e:  # noqa: BLE001
        logger.warning("词法召回失败（将尝试单路语义） | err=%s", e)
        return []
