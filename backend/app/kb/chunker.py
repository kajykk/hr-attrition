"""标题感知切分器（feat/rag-kb）.

策略（对应简历关键词 "Chunking 策略"）：
  - token 估算：CJK 字符按 1 token/字，连续拉丁词按 ~0.75 token/字符（工程近似）
  - 先按段落聚合到 ≤ max_tokens；单段超限时滑动窗口切分（窗口 max_tokens，步进 overlap）
  - heading_path 随切片携带（检索命中可展示出处章节）
  - 表格行（| 开头或制表符分隔）视为一个不可切原子块
"""
import re
from dataclasses import dataclass

from app.core.config import settings
from app.kb.parsers import RawSection

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
_TABLE_RE = re.compile(r"^\s*(\|.*\||.*\t.*)$")

# 简化表格判定：块内含 ≥2 个 | 分隔行即视为表格
# [^\S\n] = 除换行外的空白（避免 \s* 吞掉分隔符导致回溯漏匹配）
_TABLE_BLOCK_RE = re.compile(r"(?:^|\n)[^\S\n]*\|.+\|[^\S\n]*(?:\n[^\S\n]*\|.+\|[^\S\n]*)+")


@dataclass
class Chunk:
    """最终入库切片."""

    content: str
    heading_path: str  # "第三章 薪酬 > 3.2 调薪流程"
    seq: int
    token_count: int


def estimate_tokens(text: str) -> int:
    """CJK 按字计，其余按空白词计（工程近似，避免引入 tokenizer 重依赖）."""
    cjk = len(_CJK_RE.findall(text))
    rest = _CJK_RE.sub(" ", text)
    words = len(rest.split())
    return cjk + words


def split_document(sections: list[RawSection]) -> list[Chunk]:
    """将解析后的段落切分为入库切片."""
    max_tokens = settings.RAG_CHUNK_TOKENS
    overlap = min(settings.RAG_CHUNK_OVERLAP, max_tokens // 2)

    chunks: list[Chunk] = []
    for section in sections:
        heading = " > ".join(section.heading_path)
        for block in _atomic_blocks(section.text):
            block_tokens = estimate_tokens(block)
            if block_tokens <= max_tokens:
                chunks.append((block, heading, block_tokens))
                continue
            # 超长块：滑动窗口切分
            for window in _sliding_windows(block, max_tokens, overlap):
                tokens = estimate_tokens(window)
                if tokens >= 8:  # 过滤碎屑窗口
                    chunks.append((window, heading, tokens))

    return [
        Chunk(content=c.strip(), heading_path=h, seq=i, token_count=t)
        for i, (c, h, t) in enumerate(chunks)
    ]


def _atomic_blocks(text: str) -> list[str]:
    """段落聚合为原子块：表格块保持完整，普通文本按行组聚合."""
    blocks: list[str] = []
    pos = 0
    for match in _TABLE_BLOCK_RE.finditer(text):
        before = text[pos : match.start()].strip()
        if before:
            blocks.extend(b for b in before.split("\n\n") if b.strip())
        blocks.append(match.group(0).strip())
        pos = match.end()
    tail = text[pos:].strip()
    if tail:
        blocks.extend(b for b in tail.split("\n\n") if b.strip())
    return blocks or ([text] if text.strip() else [])


def _sliding_windows(text: str, max_tokens: int, overlap: int):
    """按句子边界优先的滑动窗口切分.

    以句号/问号/换行为切割点贪心装窗；无标点长文按硬截断兜底。
    """
    sentences = re.split(r"(?<=[。！？!?；;\n])", text)
    windows: list[str] = []
    buf: list[str] = []
    buf_tokens = 0

    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue
        s_tokens = estimate_tokens(s)
        # 单句超限：硬截断兜底
        if s_tokens > max_tokens:
            if buf:
                windows.append("".join(buf))
                buf, buf_tokens = [], 0
            step = max(max_tokens - overlap, 1)
            for i in range(0, len(s), step):
                piece = s[i : i + max_tokens]
                if piece.strip():
                    windows.append(piece)
            continue

        if buf_tokens + s_tokens > max_tokens and buf:
            windows.append("".join(buf))
            # 回退 overlap 个 token 的尾部作为下一窗口开头
            tail: list[str] = []
            tail_tokens = 0
            for prev in reversed(buf):
                t = estimate_tokens(prev)
                if tail_tokens + t > overlap:
                    break
                tail.insert(0, prev)
                tail_tokens += t
            buf, buf_tokens = list(tail), tail_tokens

        buf.append(s)
        buf_tokens += s_tokens

    if buf:
        windows.append("".join(buf))
    return windows
