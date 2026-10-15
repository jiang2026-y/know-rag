"""员工访问管理迁移：创建幂等记录和六个月保留的权限变更审计表。

定义 upgrade 和 downgrade；upgrade 创建管理员写接口所需的幂等、审计表及过期清理索引，downgrade 仅删除本迁移新增表。
"""

from alembic import op
import sqlalchemy as sa


revision: str = "20260804_0002"
down_revision: str | None = "20260725_0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """创建 24 小时幂等记录与 6 个月员工访问审计记录。"""
    op.create_table(
        "api_idempotency_record",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_emp_id", sa.String(64), nullable=False),
        sa.Column("endpoint", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("response_payload", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "actor_emp_id", "endpoint", "idempotency_key", name="uk_api_idempotency_actor_endpoint_key"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_api_idempotency_expires_at", "api_idempotency_record", ["expires_at"])
    op.create_table(
        "staff_access_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_emp_id", sa.String(64), nullable=False),
        sa.Column("target_emp_id", sa.String(64), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=False),
        sa.Column("after_state", sa.JSON(), nullable=False),
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
    op.create_index("idx_staff_access_audit_target_created", "staff_access_audit", ["target_emp_id", "created_at"])
    op.create_index("idx_staff_access_audit_expires_at", "staff_access_audit", ["expires_at"])


def downgrade() -> None:
    """删除本迁移新增的审计与幂等表。"""
    op.drop_index("idx_staff_access_audit_expires_at", table_name="staff_access_audit")
    op.drop_index("idx_staff_access_audit_target_created", table_name="staff_access_audit")
    op.drop_table("staff_access_audit")
    op.drop_index("idx_api_idempotency_expires_at", table_name="api_idempotency_record")
    op.drop_table("api_idempotency_record")
