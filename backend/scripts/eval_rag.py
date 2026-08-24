"""RAG 检索质量离线评估（feat/rag-kb）.

用法（需 PostgreSQL + 已索引样例文档）：
    python scripts/eval_rag.py --tenant-id <UUID> [--golden data/golden_set.jsonl] [--out eval_report.json]

流程：
  1. 将 backend/data/kb_sample/*.md 上传并等待索引就绪（或复用已索引文档）
  2. 逐题调用混合检索，统计 Recall@5 / MRR
  3. 对 answerable=false 题目校验拒答行为（refusal accuracy）
  4. 端到端延迟采样（retrieval 阶段），输出 P50/P95

产出：eval_report.json —— 简历量化指标的直接来源。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402
from app.kb.chunker import split_document  # noqa: E402
from app.kb.embedding_client import embed_texts  # noqa: E402
from app.kb.parsers import RawSection, parse_markdown  # noqa: E402
from app.kb.retriever import hybrid_search  # noqa: E402
from app.kb.service import scan_and_mask_pii  # noqa: E402
from app.kb.tokenizer_zh import tokenize_for_index  # noqa: E402
from app.models.kb import KBChunk, KBDocument  # noqa: E402


def _load_golden(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


async def _ensure_sample_docs(tenant_id: str) -> None:
    """将样例制度文档入库（幂等：按文件名 title 去重）."""
    sample_dir = Path(__file__).resolve().parent.parent / "data" / "kb_sample"
    async with async_session_factory() as session:
        for md_file in sorted(sample_dir.glob("*.md")):
            exists = (
                await session.execute(
                    select(KBDocument.id).where(
                        KBDocument.tenant_id == tenant_id,
                        KBDocument.title == md_file.stem,
                    )
                )
            ).scalar_one_or_none()
            if exists:
                continue
            # 评估场景：uploaded_by 以租户 ID 充当操作人（NOT NULL 约束）
            doc = KBDocument(
                tenant_id=tenant_id,
                title=md_file.stem,
                file_type="md",
                file_hash=f"eval-{md_file.stem}",
                status="processing",
                uploaded_by=tenant_id,
            )
            session.add(doc)
            await session.flush()

            sections: list[RawSection] = []
            for sec in parse_markdown(md_file.read_bytes()):
                masked, _hits = scan_and_mask_pii(sec.text)
                sections.append(RawSection(heading_path=sec.heading_path, text=masked))

            chunks = split_document(sections)
            vectors = await embed_texts([c.content for c in chunks])
            await session.execute(delete(KBChunk).where(KBChunk.document_id == doc.id))
            rows = [
                {
                    "document_id": doc.id,
                    "tenant_id": doc.tenant_id,
                    "seq": c.seq,
                    "content": c.content,
                    "heading_path": c.heading_path[:500],
                    "token_count": c.token_count,
                    "embedding": v,
                    "tsv": func.to_tsvector("simple", tokenize_for_index(c.content)),
                }
                for c, v in zip(chunks, vectors)
            ]
            if rows:
                await session.execute(pg_insert(KBChunk).values(rows))
            doc.status = "ready"
            doc.chunk_count = len(chunks)
        await session.commit()


async def evaluate(tenant_id: str, golden: list[dict]) -> dict:
    """逐题评估检索命中与拒答."""
    recall_hits = 0
    mrr_total = 0.0
    refusal_correct = 0
    answerable_count = 0
    unanswerable_count = 0
    latencies: list[int] = []
    misses: list[dict] = []

    for item in golden:
        started = time.perf_counter()
        async with async_session_factory() as session:
            chunks = await hybrid_search(session, tenant_id, item["question"], top_k=5)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        latencies.append(elapsed_ms)

        if not item["answerable"]:
            unanswerable_count += 1
            # 无可信命中即视为正确拒答（生成层还会二次兜底）
            refused = not chunks
            refusal_correct += int(refused)
            continue

        answerable_count += 1
        hit_rank = 0
        for rank, chunk in enumerate(chunks, start=1):
            if chunk.document_title == item["expected_doc"]:
                hit_rank = rank
                break
        if hit_rank:
            recall_hits += 1
            mrr_total += 1.0 / hit_rank
        else:
            misses.append({"question": item["question"], "expected": item["expected_doc"]})

    latencies.sort()

    def _pct(p: float) -> int:
        idx = min(int(p * (len(latencies) - 1)), max(len(latencies) - 1, 0))
        return latencies[idx] if latencies else 0

    report = {
        "total_questions": len(golden),
        "recall_at_5": round(recall_hits / max(answerable_count, 1), 4),
        "mrr": round(mrr_total / max(answerable_count, 1), 4),
        "refusal_accuracy": round(refusal_correct / max(unanswerable_count, 1), 4),
        "p95_latency_ms": {"retrieval_p50": _pct(0.5), "retrieval_p95": _pct(0.95)},
        "answerable": answerable_count,
        "unanswerable": unanswerable_count,
        "misses": misses[:10],
        "_note": "latency 仅含检索阶段；端到端 P95 由 query 接口 latency_ms 字段另行采集",
    }
    return report


async def main_async(args: argparse.Namespace) -> dict:
    from app.core.config import settings

    if not settings.DATABASE_URL.startswith(("postgresql", "postgres")):
        raise SystemExit("eval 需要 PostgreSQL DATABASE_URL")

    golden = _load_golden(Path(args.golden))
    print(f"[1/3] 载入黄金集 {len(golden)} 题")
    await _ensure_sample_docs(args.tenant_id)
    print("[2/3] 样例文档已索引")
    report = await evaluate(args.tenant_id, golden)
    print("[3/3] 评估完成")
    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写入 {out_path}")
    print(json.dumps({k: v for k, v in report.items() if k != "misses"}, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索质量离线评估")
    parser.add_argument("--tenant-id", required=True, help="评估用租户 UUID")
    parser.add_argument("--golden", default=str(Path(__file__).parent.parent / "data" / "golden_set.jsonl"))
    parser.add_argument("--out", default=str(Path(__file__).parent / "eval_report.json"))
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()

