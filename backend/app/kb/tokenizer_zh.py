"""中文分词 + PostgreSQL tsvector/tsquery 构造（feat/rag-kb）.

方案：jieba cut_for_search 预分词，空格拼接后用 to_tsvector('simple', ...) 入库；
     查询侧同样分词后以 & 连接构造 to_tsquery。词法召回排名用 ts_rank_cd。
"""
import re

from app.core.logging import get_logger

logger = get_logger(__name__)

# tsquery 特殊字符（必须剥离，防语法注入/解析错误）
_TSQUERY_SPECIAL = re.compile(r"[^\u4e00-\u9fffa-zA-Z0-9]+")

_jieba = None


def _get_jieba():
    """延迟加载 jieba（未安装 rag 依赖组时给出明确报错）."""
    global _jieba
    if _jieba is None:
        try:
            import jieba

            jieba.setLogLevel(60)  # 关闭建词日志噪声
            _jieba = jieba
        except ImportError as e:
            raise RuntimeError("RAG 依赖缺失：请安装 .[rag]（缺 jieba）") from e
    return _jieba


def tokenize(text: str, max_tokens: int = 256) -> list[str]:
    """jieba 搜索引擎模式分词，去停用性单字符与纯标点."""
    jieba = _get_jieba()
    tokens: list[str] = []
    for tok in jieba.cut_for_search(text or ""):
        tok = tok.strip()
        if not tok or _TSQUERY_SPECIAL.fullmatch(tok):
            continue
        tokens.append(tok.lower())
        if len(tokens) >= max_tokens:
            break
    return tokens


def tokenize_for_index(text: str) -> str:
    """入库用：空格拼接（配合 to_tsvector('simple', :tokens)）."""
    return " ".join(tokenize(text))


def build_tsquery(text: str) -> str:
    """查询用：词元加双引号后以 & 连接（'薪酬' & '调整'），特殊字符已剥离.

    若全部词元被过滤则返回空串，调用方应跳过词法召回。
    """
    quoted = []
    for tok in tokenize(text):
        safe = _TSQUERY_SPECIAL.sub("", tok)
        if safe:
            quoted.append(f"'{safe}'")
    return " & ".join(quoted)


def sanitize_for_like(text: str) -> str:
    """LIKE/ILIKE 通配符转义（% _ \），防御式编码."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    logger.debug("sanitize_for_like: %d chars", len(escaped))
    return escaped
