"""RAG 问答工作流：LangGraph 状态机 + 节点实现（feat/rag-kb）.

架构说明（单一逻辑源原则）：
  - 四个节点函数 retrieve/rerank/generate/self_check 是唯一业务实现
  - 非流式路径：build_graph() 将节点装配为 LangGraph StateGraph，经 graph.ainvoke 驱动
  - 流式路径：service.query_stream 按相同顺序直接驱动节点函数，
    generate 节点通过 state["sink"] 回调逐 token 外送 SSE
  - 两驱动共用节点函数，避免"流式/非流式双实现漂移"

状态机：
  START → retrieve → rerank(可选跳过) → generate → self_check ─┬→ END
                                    ↑______ 引用校验失败且 attempt<1 ___|
"""
import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any, TypedDict

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.kb import prompts
from app.kb.retriever import RetrievedChunk, hybrid_search

logger = get_logger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MAX_ATTEMPTS = 2  # generate + 至多一次纠偏重生成


class RagState(TypedDict, total=False):
    """工作流共享状态."""

    question: str
    tenant_id: str
    chunks: list[RetrievedChunk]
    answer: str
    citations: list[int]
    refused: bool
    attempt: int
    sink: Any  # async callable(dict) | None —— SSE token 外送通道


# ---------------------------------------------------------------- 节点实现
async def retrieve_node(state: RagState) -> RagState:
    """检索节点：混合召回；无命中即置拒答."""
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        chunks = await hybrid_search(session, state["tenant_id"], state["question"])
    updates: RagState = {"chunks": chunks, "refused": not chunks}
    if not chunks:
        updates["answer"] = prompts.REFUSAL_ANSWER
        updates["citations"] = []
    return {**state, **updates}


async def rerank_node(state: RagState) -> RagState:
    """重排已在 retriever.hybrid_search 内完成，此节点保留为显式阶段（便于观测与扩展）."""
    return state


async def generate_node(state: RagState) -> RagState:
    """生成节点：Qwen 主 → DeepSeek 备 → 异常时拒答话术兜底；支持 sink 流式外送."""
    if state.get("refused"):
        return state

    chunks = state["chunks"]
    prompt = prompts.build_user_prompt(
        state["question"],
        [c.to_prompt_dict(i + 1) for i, c in enumerate(chunks)],
    )
    correction = (
        prompts.CORRECTION_SUFFIX if state.get("attempt", 1) > 1 else ""
    )
    user_content = prompt + correction

    answer_parts: list[str] = []
    model_used = ""
    for model in (settings.LLM_PRIMARY, settings.LLM_FALLBACK):
        try:
            async for event in _stream_llm(model, user_content):
                kind = event.get("type")
                if kind == "token":
                    answer_parts.append(event["text"])
                    sink = state.get("sink")
                    if sink is not None:
                        await sink({"type": "token", "text": event["text"]})
                elif kind == "done":
                    break
            model_used = model
            break
        except (httpx.HTTPError, json.JSONDecodeError, RuntimeError) as e:
            logger.warning("RAG 生成调用 %s 失败：%s", model, e)
            answer_parts.clear()
            continue

    answer = "".join(answer_parts).strip() or prompts.REFUSAL_ANSWER
    if not model_used:
        # 主备全挂：拒答话术（复用三级回退的末级语义）
        sink = state.get("sink")
        if sink is not None:
            await sink({"type": "token", "text": answer})
    return {**state, "answer": answer}


async def self_check_node(state: RagState) -> RagState:
    """自检节点：引用编号必须落在资料范围内；失败且未超次数则回环重生成."""
    if state.get("refused") or not state.get("answer"):
        return state
    attempt = state.get("attempt", 1)
    valid_ids = {i + 1 for i in range(len(state["chunks"]))}
    _, invalid = prompts.extract_citations(state["answer"], valid_ids)

    if invalid and attempt < MAX_ATTEMPTS:
        logger.info("引用校验失败(invalid=%s)，触发纠偏重生成", invalid[:5])
        retry = dict(state)
        retry["attempt"] = attempt + 1
        retry.pop("answer", None)
        return await generate_node(retry)

    cleaned = prompts.strip_invalid_citations(state["answer"], valid_ids)
    cited = sorted(prompts.extract_citations(cleaned, valid_ids)[0])
    return {**state, "answer": cleaned, "citations": cited, "attempt": attempt}


# ---------------------------------------------------------------- LLM 调用
async def _stream_llm(model: str, user_content: str) -> AsyncGenerator[dict, None]:
    """DashScope OpenAI 兼容 SSE 调用（与 llm_service 同款管道，独立 system prompt）."""
    if not settings.DASHSCOPE_API_KEY:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")
    headers = {
        "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": True,
    }
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client, client.stream(
        "POST", f"{DASHSCOPE_BASE_URL}/chat/completions", headers=headers, json=payload
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            obj = json.loads(data)
            choices = obj.get("choices") or []
            if choices:
                content = choices[0].get("delta", {}).get("content")
                if content:
                    yield {
                        "type": "token",
                        "text": content,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                    }
    yield {"type": "done"}


# ---------------------------------------------------------------- LangGraph 装配
_compiled_graph = None


def build_graph():
    """将节点装配为 LangGraph StateGraph（懒加载编译，进程内单例）.

    边：retrieve → rerank → generate → self_check → END；
        self_check 内部通过直接调用 generate_node 实现至多一次纠偏回环。
    """
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as e:
        raise RuntimeError("RAG 依赖缺失：请安装 .[rag]（缺 langgraph）") from e

    builder = StateGraph(RagState)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("rerank", rerank_node)
    builder.add_node("generate", generate_node)
    builder.add_node("self_check", self_check_node)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "rerank")
    builder.add_edge("rerank", "generate")
    builder.add_edge("generate", "self_check")
    builder.add_edge("self_check", END)
    _compiled_graph = builder.compile()
    return _compiled_graph


async def run_stream_pipeline(initial_state: RagState) -> AsyncGenerator[dict, None]:
    """流式驱动器：按图顺序执行节点，token 经 sink 外送为 SSE 事件.

    与 build_graph 共用同一批节点函数。
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def sink(event: dict) -> None:
        await queue.put({"type": "token", "text": event["text"]})

    async def _drive() -> None:
        state = {**initial_state, "sink": sink}
        state = await retrieve_node(state)
        if not state.get("refused"):
            state = await rerank_node(state)
            state = await generate_node(state)
        state = await self_check_node(state)
        citations = [
            {
                "index": c.seq + 1,
                "title": c.document_title,
                "heading_path": c.heading_path,
                "snippet": c.content[:120],
            }
            for c in state.get("chunks", [])
        ]
        await queue.put(
            {
                "type": "done",
                "answer": state.get("answer", ""),
                "citations": citations,
                "refused": bool(state.get("refused")),
            }
        )
        await queue.put(None)

    driver = asyncio.create_task(_drive())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
            if event["type"] == "done":
                break
    finally:
        driver.cancel()
