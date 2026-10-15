"""目标标准 schema 基线迁移：创建汽车领域表，并把现有知识库数据无损迁入文档版本结构。

定义 upgrade、downgrade 及其辅助函数；upgrade 会备份旧表、创建目标表、迁移文档/分片/任务数据，
并将历史文档权限安全地补为 VISITOR；downgrade 会拒绝自动回退，避免误删已迁移的数据。
"""
from collections.abc import Iterable

from alembic import op
import sqlalchemy as sa


revision: str = "20260725_0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def _tables() -> set[str]:
    """返回当前数据库已存在的表名。"""
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    """返回指定表的列名，供兼容旧 schema 的条件迁移使用。"""
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    """返回指定表的索引名。"""
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _backup_table(table_name: str) -> None:
    """在就地迁移前保留一次性数据快照，供人工核验或恢复使用。"""
    backup_name = f"{table_name}__pre_alembic_20260725_0001"
    if backup_name not in _tables():
        op.execute(
            sa.text(
                f"CREATE TABLE `{backup_name}` AS SELECT * FROM `{table_name}`"
            )
        )


def _add_columns(table_name: str, columns: Iterable[sa.Column[object]]) -> None:
    """仅为缺失的目标字段执行新增操作。"""
    existing = _columns(table_name)
    for column in columns:
        if column.name not in existing:
            op.add_column(table_name, column)


def _create_knowledge_document() -> None:
    """创建目标知识文档主表。"""
    op.create_table(
        "knowledge_document",
        sa.Column("doc_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("doc_title", sa.String(1024), nullable=False),
        sa.Column("expire_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("accessible_by", sa.String(1024), nullable=True),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("knowledge_base_type", sa.String(32), nullable=True),
        sa.Column("extension", sa.Text(), nullable=True),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted", sa.SmallInteger(), nullable=False, server_default="0"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_status", "knowledge_document", ["status"])
    op.create_index("idx_status_doc_id", "knowledge_document", ["status", "doc_id"])
    op.create_index("idx_created_at", "knowledge_document", ["created_at"])


def _create_knowledge_document_version() -> None:
    """创建文档版本快照表。"""
    op.create_table(
        "knowledge_document_version",
        sa.Column("version_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("doc_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("doc_url", sa.String(2048), nullable=True),
        sa.Column("converted_doc_url", sa.String(2048), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("upload_user", sa.String(255), nullable=True),
        sa.Column("changelog", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("doc_id", "version", name="uk_doc_version"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_doc_id", "knowledge_document_version", ["doc_id"])
    op.create_index("idx_content_hash", "knowledge_document_version", ["content_hash"])


def _create_knowledge_segment() -> None:
    """创建支持文档版本和权限元数据的知识片段表。"""
    op.create_table(
        "knowledge_segment",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("text", sa.Text(length=4294967295), nullable=False),
        sa.Column("chunk_id", sa.String(255), nullable=False),
        sa.Column("metadata", sa.String(2048), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("document_version", sa.BigInteger(), nullable=True),
        sa.Column("chunk_order", sa.Integer(), nullable=False),
        sa.Column("embedding_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(255), nullable=True),
        sa.Column("skip_embedding", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("chunk_id", name="uk_knowledge_segment_chunk_id"),
        sa.UniqueConstraint("document_id", "chunk_order", name="uk_knowledge_segment_document_chunk_order"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_document_id", "knowledge_segment", ["document_id"])
    op.create_index("idx_document_id_chunk_order", "knowledge_segment", ["document_id", "chunk_order"])
    op.create_index("idx_document_status_skip", "knowledge_segment", ["document_id", "status", "skip_embedding"])
    op.create_index("idx_document_version", "knowledge_segment", ["document_version"])
    op.create_index("idx_status", "knowledge_segment", ["status"])


def _create_document_processing_task() -> None:
    """创建保留重试、错误和生命周期信息的运行时文档任务表。"""
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
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_document_id", "document_processing_task", ["document_id"])
    op.create_index("idx_version_id", "document_processing_task", ["version_id"])
    op.create_index("idx_state_updated_at", "document_processing_task", ["state", "updated_at"])


def _create_chat_tables() -> None:
    """创建目标会话和消息表。"""
    op.create_table(
        "chat_conversation",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.UniqueConstraint("conversation_id", name="uk_conversation_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_user_id_status", "chat_conversation", ["user_id", "status"])
    op.create_index("idx_updated_at", "chat_conversation", ["updated_at"])
    op.create_table(
        "chat_message",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(length=4294967295), nullable=True),
        sa.Column("transform_content", sa.Text(length=4294967295), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("rag_references", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.UniqueConstraint("message_id", name="uk_message_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_conversation_id", "chat_message", ["conversation_id"])
    op.create_index("idx_created_at", "chat_message", ["created_at"])


def _create_vehicle_and_identity_tables() -> None:
    """创建车型、车辆、订单、员工和客户身份数据表。"""
    op.execute(
        """
        CREATE TABLE car_info (
            id BIGINT NOT NULL AUTO_INCREMENT, info_id VARCHAR(64) NOT NULL,
            brand VARCHAR(64), model_name VARCHAR(128), model_year INT, version VARCHAR(128),
            full_name VARCHAR(256), vehicle_type VARCHAR(32), fuel_type VARCHAR(32), seat_count INT,
            displacement DECIMAL(4,1), motor_power DECIMAL(8,2), range_km INT,
            guide_price DECIMAL(12,2), color_options VARCHAR(512), dimensions VARCHAR(64),
            wheelbase INT, manufacturer VARCHAR(128), status VARCHAR(32) DEFAULT '在售',
            image_url VARCHAR(512), description TEXT, remark TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id), UNIQUE KEY uk_info_id (info_id), KEY idx_brand (brand),
            KEY idx_vehicle_type (vehicle_type), KEY idx_fuel_type (fuel_type),
            KEY idx_status (status), KEY idx_full_name (full_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE my_car (
            id BIGINT NOT NULL AUTO_INCREMENT, car_id VARCHAR(64) NOT NULL,
            user_id VARCHAR(64), car_info_id VARCHAR(64), nickname VARCHAR(128),
            full_name VARCHAR(256), image_url VARCHAR(512), order_id VARCHAR(64),
            plate_number VARCHAR(32) NOT NULL, color VARCHAR(32), vin VARCHAR(32),
            engine_number VARCHAR(64), purchase_date DATE, purchase_price DECIMAL(15,2),
            mileage INT DEFAULT 0, register_date DATE, insurance_expire_date DATE,
            inspection_expire_date DATE, remark TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id), UNIQUE KEY uk_car_id (car_id),
            UNIQUE KEY uk_plate_number (plate_number), KEY idx_user_id (user_id),
            KEY idx_car_info_id (car_info_id), KEY idx_order_id (order_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE car_order (
            id BIGINT NOT NULL AUTO_INCREMENT, order_id VARCHAR(64) NOT NULL,
            user_id VARCHAR(64), car_id VARCHAR(64), order_no VARCHAR(64) NOT NULL,
            order_type VARCHAR(32), order_status VARCHAR(32) DEFAULT '待支付', brand VARCHAR(64),
            model VARCHAR(128), color VARCHAR(32), vin VARCHAR(32), seller_name VARCHAR(256),
            seller_contact VARCHAR(128), vehicle_price DECIMAL(15,2), purchase_tax DECIMAL(15,2),
            insurance_fee DECIMAL(15,2), other_fee DECIMAL(15,2), total_amount DECIMAL(15,2),
            discount_amount DECIMAL(15,2), actual_amount DECIMAL(15,2), payment_method VARCHAR(32),
            order_date DATE, delivery_date DATE, remark TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id), UNIQUE KEY uk_order_id (order_id), UNIQUE KEY uk_order_no (order_no),
            KEY idx_user_id (user_id), KEY idx_car_id (car_id), KEY idx_order_status (order_status),
            KEY idx_order_type (order_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE staff_info (
            id BIGINT NOT NULL AUTO_INCREMENT, emp_id VARCHAR(64), name VARCHAR(64),
            password VARCHAR(128), job VARCHAR(128), entry_time DATE, birthday DATE,
            educational_background VARCHAR(32), director_id BIGINT, dept_id BIGINT,
            duty VARCHAR(512), motto VARCHAR(256), pic_url VARCHAR(512),
            status VARCHAR(32) DEFAULT 'ON_JOB', resignation_time DATE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id), UNIQUE KEY uk_emp_id (emp_id), KEY idx_dept_id (dept_id),
            KEY idx_director_id (director_id), KEY idx_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE user_info (
            id BIGINT NOT NULL AUTO_INCREMENT, phone VARCHAR(20) NOT NULL,
            password VARCHAR(128) NOT NULL, name VARCHAR(64), nickname VARCHAR(128),
            avatar VARCHAR(512), status VARCHAR(32) DEFAULT 'ACTIVE',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id), UNIQUE KEY uk_phone (phone)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def _create_table_meta() -> None:
    """创建动态数据查询表的元数据表。"""
    op.execute(
        """
        CREATE TABLE table_meta (
            id BIGINT NOT NULL AUTO_INCREMENT, table_name VARCHAR(128) NOT NULL,
            description VARCHAR(512), create_sql TEXT, columns_info TEXT, version_id BIGINT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            lock_version INT NOT NULL DEFAULT 0, deleted TINYINT NOT NULL DEFAULT 0,
            PRIMARY KEY (id), UNIQUE KEY uk_table_name (table_name), KEY idx_version_id (version_id),
            KEY idx_created_at (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def _normalize_document_statuses() -> None:
    """将旧实现中的小写状态统一为目标 schema 的大写状态值。"""
    op.execute(
        """
        UPDATE knowledge_document
        SET status = CASE LOWER(status)
            WHEN 'uploaded' THEN 'UPLOADED'
            WHEN 'converting' THEN 'CONVERTING'
            WHEN 'converted' THEN 'CONVERTED'
            WHEN 'chunked' THEN 'CHUNKED'
            WHEN 'vector_stored' THEN 'VECTOR_STORED'
            WHEN 'failed' THEN 'FAILED'
            ELSE UPPER(status)
        END
        """
    )


def _migrate_existing_knowledge_document() -> None:
    """把旧文档行迁入版本表，并移除已被版本表替代的旧字段。"""
    _backup_table("knowledge_document")
    _add_columns(
        "knowledge_document",
        [
            sa.Column("expire_date", sa.Date(), nullable=True),
            sa.Column("description", sa.String(512), nullable=True),
            sa.Column("knowledge_base_type", sa.String(32), nullable=True),
            sa.Column("extension", sa.Text(), nullable=True),
            sa.Column("current_version_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
            sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("deleted", sa.SmallInteger(), nullable=False, server_default="0"),
        ],
    )
    _normalize_document_statuses()
    op.execute(
        "UPDATE knowledge_document SET accessible_by = 'VISITOR' "
        "WHERE accessible_by IS NULL OR TRIM(accessible_by) = ''"
    )
    op.execute(
        """
        INSERT INTO knowledge_document_version
            (doc_id, version, doc_url, converted_doc_url, status, upload_user)
        SELECT doc_id, '1.0.0', doc_url, converted_doc_url, status, upload_user
        FROM knowledge_document
        """
    )
    op.execute(
        """
        UPDATE knowledge_document document
        JOIN knowledge_document_version version
          ON version.doc_id = document.doc_id AND version.version = '1.0.0'
        SET document.current_version_id = version.version_id
        """
    )
    existing_indexes = _indexes("knowledge_document")
    if "idx_knowledge_document_upload_user" in existing_indexes:
        op.drop_index("idx_knowledge_document_upload_user", table_name="knowledge_document")
    legacy_columns = _columns("knowledge_document")
    for column_name in ("upload_user", "doc_url", "converted_doc_url"):
        if column_name in legacy_columns:
            op.drop_column("knowledge_document", column_name)
    existing_indexes = _indexes("knowledge_document")
    if "idx_status" not in existing_indexes:
        op.create_index("idx_status", "knowledge_document", ["status"])
    if "idx_status_doc_id" not in existing_indexes:
        op.create_index("idx_status_doc_id", "knowledge_document", ["status", "doc_id"])
    if "idx_created_at" not in existing_indexes:
        op.create_index("idx_created_at", "knowledge_document", ["created_at"])


def _migrate_existing_knowledge_segment() -> None:
    """补充版本和权限元数据，并将旧 JSON 元数据列转换为目标字符串列。"""
    _backup_table("knowledge_segment")
    _add_columns(
        "knowledge_segment",
        [
            sa.Column("document_version", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
            sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("deleted", sa.SmallInteger(), nullable=False, server_default="0"),
        ],
    )
    op.execute(
        """
        UPDATE knowledge_segment segment
        JOIN knowledge_document document ON document.doc_id = segment.document_id
        SET segment.document_version = document.current_version_id,
            segment.metadata = JSON_SET(
                COALESCE(segment.metadata, JSON_OBJECT()),
                '$.accessibleBy', document.accessible_by
            )
        """
    )
    op.alter_column(
        "knowledge_segment",
        "metadata",
        existing_type=sa.JSON(),
        type_=sa.String(2048),
        existing_nullable=True,
    )
    op.alter_column(
        "knowledge_segment",
        "skip_embedding",
        existing_type=sa.SmallInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
        server_default="0",
    )
    existing_indexes = _indexes("knowledge_segment")
    if "idx_document_version" not in existing_indexes:
        op.create_index("idx_document_version", "knowledge_segment", ["document_version"])
    op.execute(
        "UPDATE knowledge_segment SET status = 'INIT', embedding_id = NULL"
    )
    op.execute(
        """
        UPDATE knowledge_document document
        JOIN knowledge_segment segment ON segment.document_id = document.doc_id
        JOIN knowledge_document_version version
          ON version.version_id = document.current_version_id
        SET document.status = 'CHUNKED', version.status = 'CHUNKED'
        """
    )


def _migrate_existing_document_processing_task() -> None:
    """为既有运行时任务补充所属文档版本。"""
    _backup_table("document_processing_task")
    _add_columns(
        "document_processing_task",
        [sa.Column("version_id", sa.BigInteger(), nullable=True)],
    )
    op.execute(
        """
        UPDATE document_processing_task task
        JOIN knowledge_document document ON document.doc_id = task.document_id
        SET task.version_id = document.current_version_id
        WHERE task.version_id IS NULL
        """
    )
    op.alter_column(
        "document_processing_task",
        "version_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    if "idx_version_id" not in _indexes("document_processing_task"):
        op.create_index("idx_version_id", "document_processing_task", ["version_id"])


def _migrate_existing_chat_tables() -> None:
    """把空的旧聊天表升级为目标审计字段与时间字段命名。"""
    for table_name in ("chat_conversation", "chat_message"):
        _backup_table(table_name)
        columns = _columns(table_name)
        if "create_time" in columns:
            op.execute(
                f"ALTER TABLE `{table_name}` CHANGE COLUMN create_time created_at "
                "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            )
        columns = _columns(table_name)
        if "update_time" in columns:
            op.execute(
                f"ALTER TABLE `{table_name}` CHANGE COLUMN update_time updated_at "
                "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
            )
        _add_columns(
            table_name,
            [
                sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("deleted", sa.SmallInteger(), nullable=False, server_default="0"),
            ],
        )
    if "chat_conversation" in _tables():
        op.execute(
            """
            ALTER TABLE chat_conversation
            MODIFY conversation_id VARCHAR(64) NOT NULL,
            MODIFY user_id VARCHAR(64) NOT NULL,
            MODIFY title VARCHAR(512) NULL,
            MODIFY status VARCHAR(32) NOT NULL DEFAULT 'active'
            """
        )
    if "chat_message" in _tables():
        op.execute(
            """
            ALTER TABLE chat_message
            MODIFY message_id VARCHAR(64) NOT NULL,
            MODIFY conversation_id VARCHAR(64) NOT NULL,
            MODIFY content LONGTEXT NULL,
            MODIFY model_name VARCHAR(128) NULL
            """
        )


def upgrade() -> None:
    """创建目标 schema 并把当前开发库的数据安全迁入新版本结构。"""
    tables = _tables()
    if "knowledge_document_version" not in tables:
        _create_knowledge_document_version()
    if "knowledge_document" in tables:
        legacy_columns = _columns("knowledge_document")
        if "doc_url" in legacy_columns:
            _migrate_existing_knowledge_document()
    else:
        _create_knowledge_document()
    if "knowledge_segment" in tables:
        _migrate_existing_knowledge_segment()
    else:
        _create_knowledge_segment()
    if "document_processing_task" in tables:
        _migrate_existing_document_processing_task()
    else:
        _create_document_processing_task()
    if "chat_conversation" in tables and "chat_message" in tables:
        _migrate_existing_chat_tables()
    elif "chat_conversation" not in tables and "chat_message" not in tables:
        _create_chat_tables()
    else:
        raise RuntimeError("chat_conversation and chat_message must be migrated together")
    tables = _tables()
    if "table_meta" not in tables:
        _create_table_meta()
    if "car_info" not in tables:
        _create_vehicle_and_identity_tables()


def downgrade() -> None:
    """拒绝自动降级，避免删除已迁移的文档版本与身份数据。"""
    raise RuntimeError("This baseline migration is intentionally not reversible automatically")
