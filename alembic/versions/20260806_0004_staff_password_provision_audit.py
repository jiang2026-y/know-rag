"""HR 初始密码迁移：创建六个月保留的无敏感开户审计表。

定义 upgrade 和 downgrade；upgrade 创建内部 HR 初始密码成功操作的审计表和清理索引，
downgrade 仅删除本迁移创建的审计表。
"""

from alembic import op
import sqlalchemy as sa


revision: str = "20260806_0004"
down_revision: str | None = "20260805_0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """创建六个月保留的 HR 初始密码审计记录。"""
    op.create_table(
        "staff_password_provision_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("service_name", sa.String(64), nullable=False),
        sa.Column("target_emp_id", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index(
        "idx_staff_password_provision_audit_target_created",
        "staff_password_provision_audit",
        ["target_emp_id", "created_at"],
    )
    op.create_index(
        "idx_staff_password_provision_audit_expires_at",
        "staff_password_provision_audit",
        ["expires_at"],
    )


def downgrade() -> None:
    """删除本迁移新增的 HR 初始密码审计表。"""
    op.drop_index(
        "idx_staff_password_provision_audit_expires_at",
        table_name="staff_password_provision_audit",
    )
    op.drop_index(
        "idx_staff_password_provision_audit_target_created",
        table_name="staff_password_provision_audit",
    )
    op.drop_table("staff_password_provision_audit")
