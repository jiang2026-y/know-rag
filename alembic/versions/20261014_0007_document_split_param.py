"""切片参数迁移：为知识文档版本记录每次切片使用的策略与参数。

升级为 knowledge_document_version 增加 split_param JSON 列；历史版本保持 NULL，
表示使用与既有行为一致的标题父子默认切片。
"""

from alembic import op
import sqlalchemy as sa


revision: str = "20261014_0007"
down_revision: str | None = "20260904_0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """增加版本级切片参数列。"""
    op.add_column(
        "knowledge_document_version",
        sa.Column("split_param", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """删除版本级切片参数列。"""
    op.drop_column("knowledge_document_version", "split_param")
