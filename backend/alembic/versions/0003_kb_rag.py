"""0003: RAG 知识库表（kb_documents / kb_chunks）+ pgvector 扩展（feat/rag-kb）.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-24

说明：
  - 需要 PostgreSQL 且镜像含 pgvector 扩展（compose 已切换 pgvector/pgvector:pg15）
  - kb_chunks.tsv 由应用侧写入 to_tsvector('simple', jieba 预分词文本)
  - embedding 维度固定 1024（DashScope text-embedding-v3）
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector 扩展（幂等）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    from pgvector.sqlalchemy import Vector
    from sqlalchemy.dialects import postgresql as pg

    op.create_table(
        "kb_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False, index=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="processing",
            comment="processing/ready/failed",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pii_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "file_hash", name="uq_kb_documents_tenant_hash"),
        comment="RAG 知识库文档",
    )

    op.create_table(
        "kb_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("kb_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("heading_path", sa.String(500), nullable=True),
        sa.Column("tsv", pg.TSVECTOR(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        comment="RAG 切片：词法索引 + 语义向量",
    )
    # HNSW 语义索引 + GIN 词法索引（对应简历关键词）
    op.execute(
        "CREATE INDEX idx_kb_chunks_hnsw ON kb_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX idx_kb_chunks_gin ON kb_chunks USING gin (tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_kb_chunks_gin")
    op.execute("DROP INDEX IF EXISTS idx_kb_chunks_hnsw")
    op.drop_table("kb_chunks")
    op.drop_table("kb_documents")
    # 扩展保留（可能被其他对象引用），不做 DROP EXTENSION
