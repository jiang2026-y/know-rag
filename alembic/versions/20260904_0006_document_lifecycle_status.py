"""文档生命周期迁移：为知识文档增加独立的生命周期状态字段。"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_0006"
down_revision = "20260806_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加生命周期状态并将现有向量文档标记为启用。"""
    op.add_column(
        "knowledge_document",
        sa.Column("lifecycle_status", sa.String(16), nullable=False, server_default="ENABLED"),
    )


def downgrade() -> None:
    """删除文档生命周期状态字段。"""
    op.drop_column("knowledge_document", "lifecycle_status")
