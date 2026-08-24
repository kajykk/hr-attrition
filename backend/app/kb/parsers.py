"""文档解析：PDF / DOCX / MD / TXT → RawSection 列表（feat/rag-kb）.

设计：
  - 解析结果为"带标题路径的段落"，供 chunker 做标题感知切分
  - 表格行合并进所在段落，避免表格被切碎
  - 重依赖（pypdf/python-docx）延迟导入，未安装 rag 依赖组时给出明确报错
"""
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024


@dataclass
class RawSection:
    """解析后的原始段落：heading_path 为标题面包屑，如 ["第三章 薪酬", "3.2 调薪流程"]."""

    heading_path: list[str] = field(default_factory=list)
    text: str = ""


class ParseError(Exception):
    """文档解析失败（损坏文件/加密文件等）."""


def _require(module_name: str, hint: str) -> None:
    try:
        __import__(module_name)
    except ImportError as e:
        raise RuntimeError(f"RAG 依赖缺失：请安装 .[rag]（缺 {hint}）") from e


def parse_pdf(data: bytes) -> list[RawSection]:
    """PDF 解析：按页提取文本，页内空行分段；PDF 无可靠标题结构，heading 置空."""
    _require("pypdf", "pypdf")
    from io import BytesIO

    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        if getattr(reader, "is_encrypted", False):
            raise ParseError("PDF 已加密，无法解析")
        sections: list[RawSection] = []
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            for para in _split_paragraphs(text):
                sections.append(RawSection(heading_path=[], text=para))
        return sections
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"PDF 解析失败：{e}") from e


def parse_docx(data: bytes) -> list[RawSection]:
    """DOCX 解析：Heading 样式段落作为标题层级，普通段落归属当前标题路径."""
    _require("docx", "python-docx")
    from io import BytesIO

    import docx

    try:
        document = docx.Document(BytesIO(data))
    except Exception as e:
        raise ParseError(f"DOCX 解析失败：{e}") from e

    sections: list[RawSection] = []
    path: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            sections.append(RawSection(heading_path=list(path), text=text))
        buf.clear()

    for para in document.paragraphs:
        style = (para.style.name or "").lower() if para.style is not None else ""
        text = para.text.strip()
        if not text:
            continue
        if style.startswith("heading"):
            flush()
            level = 1
            digits = "".join(ch for ch in style if ch.isdigit())
            if digits:
                level = min(int(digits), 6)
            del path[level - 1 :]
            path.append(text[:100])
        else:
            buf.append(text)
    flush()
    return sections


def parse_markdown(data: bytes) -> list[RawSection]:
    """Markdown/TXT 解析：# 标题行驱动层级；无标题文本归入单一段落."""
    text = data.decode("utf-8", errors="replace")
    sections: list[RawSection] = []
    path: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        content = "\n".join(buf).strip()
        if content:
            sections.append(RawSection(heading_path=list(path), text=content))
        buf.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and " " in stripped:
            flush()
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip()[:100]
            del path[level - 1 :]
            path.append(title)
        elif not stripped and (not buf or not buf[-1]):
            continue
        else:
            buf.append(line.rstrip())
    flush()
    return sections


def parse_document(filename: str, data: bytes) -> list[RawSection]:
    """按扩展名分发解析器（统一入口，含大小与类型校验）."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError(f"不支持的文件类型：{ext or '未知'}（支持 {sorted(SUPPORTED_EXTENSIONS)}）")
    if len(data) == 0:
        raise ParseError("文件内容为空")
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise ParseError("文件超过 20MB 上限")

    if ext == ".pdf":
        return parse_pdf(data)
    if ext == ".docx":
        return parse_docx(data)
    return parse_markdown(data)


def _split_paragraphs(text: str) -> list[str]:
    """按空行分段，过滤纯空白段."""
    paras = [p.strip() for p in re_split_blank(text)]
    return [p for p in paras if p]


def re_split_blank(text: str) -> list[str]:
    import re

    return re.split(r"\n\s*\n+", text)
