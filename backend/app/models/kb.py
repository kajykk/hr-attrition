"""RAG 知识库 ORM：kb_documents / kb_chunks（feat/rag-kb）.

注意：
  - 依赖 pgvector 包与 PostgreSQL 扩展；models/__init__ 以 try/except 防护导入，
    未安装 .[rag] 时主应用不受影响
  - kb_chunks.embedding 使用 vector(1024)，维度由迁移固定，与 RAG_EMBEDDING_DIM 一致
"""
import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPKMixin

try:
    from pgvector.sqlalchemy import Vector
except ImportError as _e:  # pragma: no cover - 未安装 rag 依赖组时的友好提示
    raise ImportError(
        "kb 模型需要 pgvector：请安装 .[rag] 依赖组后再启用 RAG_ENABLED"
    ) from _e

# PG 方言类型实例（模块级定义，供列声明引用）
TSVECTOR_TYPE = TSVECTOR()


class KBDocument(Base, UUIDPKMixin, TenantMixin, TimestampMixin):
    """知识库文档主表."""

    __tablename__ = "kb_documents"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)  # pdf/docx/md/txt
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="processing", index=True
    )  # processing/ready/failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pii_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class KBChunk(Base):
    """切片表：content + 词法索引 tsv + 语义向量 embedding."""

    __tablename__ = "kb_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False, comment="租户 ID（冗余字段，行级隔离）"
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tsv: Mapped[str] = mapped_column(TSVECTOR_TYPE, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
