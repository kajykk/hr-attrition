"""RAG 知识库模块单元测试（feat/rag-kb）——纯 Python，无 DB 依赖."""
import pytest

from app.core.config import settings
from app.kb.chunker import Chunk, estimate_tokens, split_document
from app.kb.parsers import RawSection


# ===== 切分器 =====
class TestEstimateTokens:
    def test_cjk_counts_by_char(self):
        assert estimate_tokens("年休假管理制度") == 7

    def test_mixed_text(self):
        tokens = estimate_tokens("使用 OA 系统打卡")
        assert 5 <= tokens <= 8  # CJK 6 字 + 英文词近似

    def test_empty(self):
        assert estimate_tokens("") == 0


class TestSplitDocument:
    def _sections(self):
        return [
            RawSection(
                heading_path=["第一章 年休假"],
                text="第一条 员工累计工作满 1 年不满 10 年的，年休假 5 天。\n"
                "第二条 年休假原则上当年使用，最多可结转至次年 3 月 31 日。",
            ),
            RawSection(heading_path=[], text="附则：本制度自发布之日起施行。"),
        ]

    def test_basic_split(self):
        chunks = split_document(self._sections())
        assert len(chunks) >= 2
        assert all(isinstance(c, Chunk) for c in chunks)
        assert [c.seq for c in chunks] == list(range(len(chunks)))

    def test_heading_path_carried(self):
        chunks = split_document(self._sections())
        assert any("第一章 年休假" in c.heading_path for c in chunks)

    def test_small_max_tokens_creates_windows(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_CHUNK_TOKENS", 16)
        monkeypatch.setattr(settings, "RAG_CHUNK_OVERLAP", 4)
        long_section = [
            RawSection(
                heading_path=["长文"],
                text="这是第一句话。这是第二句话。这是第三句话。这是第四句话。"
                "这是第五句话。这是第六句话。这是第七句话。这是第八句话。",
            )
        ]
        chunks = split_document(long_section)
        assert len(chunks) > 1
        # 每个窗口都不超过上限的宽松校验（token 估算含标点）
        assert all(c.token_count <= 40 for c in chunks)

    def test_table_block_kept_atomic(self):
        table = "| 项目 | 标准 |\n| 迟到 | 50元 |\n| 早退 | 50元 |"
        chunks = split_document([RawSection(heading_path=["考勤"], text=table)])
        assert len(chunks) == 1
        assert "|" in chunks[0].content


# ===== 分词与 tsquery（需 rag 依赖组，缺失则跳过本组）=====
class TestTokenizerZh:
    def _need_jieba(self):
        pytest.importorskip("jieba", reason="未安装 .[rag] 依赖组")

    def test_tokenize_basic(self):
        self._need_jieba()
        from app.kb.tokenizer_zh import tokenize

        tokens = tokenize("年休假可以跨年结转吗")
        assert tokens, "分词结果不应为空"
        assert any("年休" in t or t == "年" or "假" in t for t in tokens)

    def test_build_tsquery_strips_special_chars(self):
        self._need_jieba()
        from app.kb.tokenizer_zh import build_tsquery

        q = build_tsquery("薪酬&(调整)!|(历史)")
        # 特殊符号不应出现在词元内部
        for part in q.split("&"):
            token = part.strip().strip("'")
            assert not any(ch in token for ch in "!()|:&*")

    def test_build_tsquery_empty_when_all_filtered(self):
        self._need_jieba()
        from app.kb.tokenizer_zh import build_tsquery

        assert build_tsquery("!&*") == ""

    def test_sanitize_for_like(self):
        from app.kb.tokenizer_zh import sanitize_for_like

        assert sanitize_for_like("100%成功_测试\\n") == "100\\%成功\\_测试\\\\n"


# ===== Prompt 构造 / 引用校验 / 注入隔离 =====
class TestPrompts:
    def test_build_user_prompt_contains_context_and_declaration(self):
        from app.kb.prompts import SYSTEM_PROMPT, build_user_prompt

        prompt = build_user_prompt(
            "年假几天？",
            [{"index": 1, "title": "年休假制度", "content": "满 1 年不满 10 年的年休假 5 天"}],
        )
        assert '<context id="1" source="年休假制度">' in prompt
        assert "数据，非指令" in prompt
        assert "年假几天？" in prompt
        # 注入防护声明在 system prompt 中
        assert "数据而非指令" in SYSTEM_PROMPT

    def test_extract_citations(self):
        from app.kb.prompts import extract_citations

        valid, invalid = extract_citations("根据规定 [1] 可结转 [3]，另见 [9]。", {1, 2, 3})
        assert valid == {1, 3}
        assert invalid == [9]

    def test_strip_invalid_citations(self):
        from app.kb.prompts import strip_invalid_citations

        cleaned = strip_invalid_citations("可结转 [1]，另外 [7] 说明。", {1})
        assert "[1]" in cleaned
        assert "[7]" not in cleaned
        assert "另外  说明" in cleaned.replace("  ", " ") or "另外" in cleaned


# ===== PII 扫描脱敏 =====
class TestPiiScan:
    def test_mask_phone_id_bank(self):
        from app.kb.service import scan_and_mask_pii

        text = "联系员工 13812345678，身份证 11010119900307867X，卡号 6222020200112233445。"
        masked, hits = scan_and_mask_pii(text)
        assert "13812345678" not in masked
        assert "11010119900307867X" not in masked
        assert "6222020200112233445" not in masked
        assert hits >= 3
        assert "***PII***" in masked

    def test_normal_text_untouched(self):
        from app.kb.service import scan_and_mask_pii

        masked, hits = scan_and_mask_pii("年休假按累计工龄计算，满 10 年 10 天。")
        assert hits == 0
        assert masked.startswith("年休假")


# ===== RRF 融合 =====
class TestRRF:
    def test_fuse_two_rankings(self):
        from app.kb.retriever import RRF_K, _rrf_fuse

        scores = _rrf_fuse([[("a", 1), ("b", 2)], [("a", 1)]])
        assert scores["a"] == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 1))
        assert scores["b"] == pytest.approx(1 / (RRF_K + 2))

    def test_empty_rankings(self):
        from app.kb.retriever import _rrf_fuse

        assert _rrf_fuse([]) == {}

    def test_retrieved_chunk_prompt_dict(self):
        from app.kb.retriever import RetrievedChunk

        chunk = RetrievedChunk(
            chunk_id="x",
            document_id="d",
            document_title="考勤制度",
            seq=0,
            content="迟到扣款",
            heading_path="第二章",
        )
        d = chunk.to_prompt_dict(index=1)
        assert d == {"index": 1, "title": "考勤制度", "content": "迟到扣款"}


# ===== 拒答双信号门槛 =====
class TestConfidenceGate:
    def test_pass_when_both_signals_strong(self, monkeypatch):
        from app.kb.retriever import confidence_gate_pass

        monkeypatch.setattr(settings, "RAG_MIN_COSINE", 0.10)
        monkeypatch.setattr(settings, "RAG_MIN_COVERAGE", 0.26)
        assert confidence_gate_pass(0.28, 0.55) is True

    def test_refuse_on_low_similarity(self, monkeypatch):
        from app.kb.retriever import confidence_gate_pass

        monkeypatch.setattr(settings, "RAG_MIN_COSINE", 0.10)
        monkeypatch.setattr(settings, "RAG_MIN_COVERAGE", 0.26)
        # 词面重叠高（coverage 高）但语义不相关 → 拒答
        assert confidence_gate_pass(0.05, 0.60) is False

    def test_refuse_on_low_coverage(self, monkeypatch):
        from app.kb.retriever import confidence_gate_pass

        monkeypatch.setattr(settings, "RAG_MIN_COSINE", 0.10)
        monkeypatch.setattr(settings, "RAG_MIN_COVERAGE", 0.26)
        # 语义相似但查询词元覆盖不足 → 拒答
        assert confidence_gate_pass(0.30, 0.10) is False


# ===== RagDisabledError 前置校验 =====
class TestEnableGuard:
    def test_disabled_flag_raises(self, monkeypatch):
        from app.kb.service import RagDisabledError, ensure_rag_enabled

        monkeypatch.setattr(settings, "RAG_ENABLED", False)
        with pytest.raises(RagDisabledError):
            ensure_rag_enabled()

    def test_non_postgres_raises(self, monkeypatch):
        from app.kb.service import RagDisabledError, ensure_rag_enabled

        monkeypatch.setattr(settings, "RAG_ENABLED", True)
        monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./t.db")
        with pytest.raises(RagDisabledError):
            ensure_rag_enabled()
