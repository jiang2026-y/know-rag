"""修复迁移：补建迁移历史已记录但实际缺失的文档处理任务表。

定义 upgrade 和 downgrade。upgrade 仅在在线执行时检测到
document_processing_task 缺失才创建表及索引；离线 SQL 模式始终渲染创建语句，
仅用于人工审核。downgrade 保持为空，避免误删迁移前可能已存在的业务表。
"""

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260805_0003"
down_revision: str | None = "20260804_0002"
branch_labels: str | None = None
depends_on: str | None = None


def _create_document_processing_task() -> None:
    """创建与基线迁移一致的文档处理任务表及其查询索引。"""
    op.create_table(
        "document_processing_task",
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("original_filename", sa.String(1024), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("document_status", sa.String(32), nullable=False),
        sa.Column("source_object_name", sa.String(1024), nullable=True),
        sa.Column("markdown_object_name", sa.String(1024), nullable=True),
        sa.Column("image_object_names", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_document_id", "document_processing_task", ["document_id"])
    op.create_index("idx_version_id", "document_processing_task", ["version_id"])
    op.create_index(
        "idx_state_updated_at", "document_processing_task", ["state", "updated_at"]
    )


def upgrade() -> None:
    """在线迁移时仅补建缺失表；离线模式输出待审核的创建 SQL。"""
    if context.is_offline_mode():
        _create_document_processing_task()
        return
    existing_tables = sa.inspect(op.get_bind()).get_table_names()
    if "document_processing_task" not in existing_tables:
        _create_document_processing_task()


def downgrade() -> None:
    """不自动删除任务表，防止回滚时删除迁移前可能已存在的业务数据。"""
    return
