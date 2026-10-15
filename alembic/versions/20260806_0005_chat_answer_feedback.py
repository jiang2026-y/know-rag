"""回答赞踩反馈迁移：创建一年保留的用户反馈表。

定义 upgrade 和 downgrade；反馈以服务端会话归属散列关联回答，保存引用、权限快照和
追踪标识，同一主体对同一回答只保留可更新的一条反馈。
"""

from alembic import op
import sqlalchemy as sa


revision: str = "20260806_0005"
down_revision: str | None = "20260806_0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """创建用户赞踩反馈表与查询、到期清理索引。"""
    op.create_table(
        "chat_answer_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("reaction", sa.String(8), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column("permission_snapshot", sa.JSON(), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("actor_id", "message_id", name="uk_chat_feedback_actor_message"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_chat_feedback_message", "chat_answer_feedback", ["message_id"])
    op.create_index("idx_chat_feedback_expires_at", "chat_answer_feedback", ["expires_at"])


def downgrade() -> None:
    """删除本迁移创建的回答反馈表。"""
    op.drop_index("idx_chat_feedback_expires_at", table_name="chat_answer_feedback")
    op.drop_index("idx_chat_feedback_message", table_name="chat_answer_feedback")
    op.drop_table("chat_answer_feedback")
