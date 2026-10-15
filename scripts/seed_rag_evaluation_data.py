"""本地 RAG 评测数据初始化脚本：创建汽车知识文档和当前车主测试车辆。

定义 EvaluationSeedRepository、DocumentSeed、seed_documents、seed_vehicle 和 main；脚本默认仅预览，
只有传入 --apply 才会写入 MySQL、Elasticsearch 并调用已配置的 Embedding 服务。所有新增记录带有
`P0测试-` 或 `EVAL-` 前缀，脚本不会覆盖已有文档、账号或绑定到其他用户的车辆。
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

import pymysql

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.document_review_service import (
    DocumentReviewService,
)
from rail_rag.modules.knowledge.application.document_vectorization_service import (
    DocumentVectorizationService,
)
from rail_rag.modules.knowledge.domain.markdown_parent_child_splitter import (
    MarkdownParentChildSplitter,
)
from rail_rag.modules.knowledge.infrastructure.dashscope_embedding_client import (
    DashScopeEmbeddingClient,
)
from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    ElasticsearchEmbeddingStore,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import (
    KnowledgeSegmentRepository,
)
from rail_rag.modules.knowledge.infrastructure.redis_distributed_lock import (
    RedisDistributedLock,
)


@dataclass(frozen=True)
class DocumentSeed:
    """一篇待写入并向量化的本地测试知识文档。"""

    title: str
    access_label: str
    markdown: str


class EvaluationSeedRepository:
    """仅供本地测试脚本使用的最小 MySQL 查询和车辆插入适配器。"""

    _TEST_CAR_ID = "EVAL-CAR-001"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def active_customer_id(self, phone: str) -> str | None:
        """按手机号查找活动顾客 ID，不读取或输出密码字段。"""
        return await asyncio.to_thread(self._active_customer_id, phone)

    async def document_by_title(self, title: str) -> tuple[int, str] | None:
        """读取同名测试文档的 ID 和状态，以支持失败后的安全续跑。"""
        return await asyncio.to_thread(self._document_by_title, title)

    async def seed_vehicle(self, customer_id: str, *, apply: bool) -> str:
        """预览或插入一辆仅绑定指定顾客的测试车，不抢占其他用户车辆。"""
        return await asyncio.to_thread(self._seed_vehicle, customer_id, apply)

    def _connect(self) -> pymysql.Connection:
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password.get_secret_value(),
            database=self._settings.mysql_database,
            charset="utf8mb4",
            autocommit=False,
        )

    def _active_customer_id(self, phone: str) -> str | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM user_info WHERE phone = %s AND status = %s LIMIT 1",
                    (phone, "ACTIVE"),
                )
                row = cursor.fetchone()
        return str(row[0]) if row is not None else None

    def _document_by_title(self, title: str) -> tuple[int, str] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT doc_id, status FROM knowledge_document WHERE doc_title = %s AND deleted = 0 LIMIT 1",
                    (title,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return int(row[0]), str(row[1])

    def _seed_vehicle(self, customer_id: str, apply: bool) -> str:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT user_id FROM my_car WHERE car_id = %s LIMIT 1",
                    (self._TEST_CAR_ID,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    if str(existing[0] or "") != customer_id:
                        raise ValueError("EVAL-CAR-001 已绑定其他用户，拒绝覆盖")
                    return "车辆已存在，跳过"
                if not apply:
                    return "将创建 EVAL-CAR-001"
                cursor.execute(
                    """
                    INSERT INTO my_car (
                        car_id, user_id, nickname, full_name, plate_number, color, vin,
                        engine_number, purchase_date, mileage, register_date,
                        insurance_expire_date, inspection_expire_date, remark
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self._TEST_CAR_ID,
                        customer_id,
                        "我的 Model 3",
                        "Tesla Model 3 2025 后轮驱动版",
                        "测A·P0001",
                        "珍珠白",
                        "EVALVIN00000000001",
                        "EVAL-ENG-0001",
                        "2025-06-18",
                        12580,
                        "2025-06-25",
                        "2026-12-31",
                        "2027-06-25",
                        "P0 本地评测测试车辆；可安全用于保险、年检、里程和车辆信息查询。",
                    ),
                )
            connection.commit()
        return "已创建 EVAL-CAR-001"


_DOCUMENTS = (
    DocumentSeed(
        title="P0测试-雨刮器更换指南",
        access_label="VISITOR",
        markdown="""# 雨刮器更换指南

## 更换前准备

车辆熄火并拉起驻车制动。确认雨刮器尺寸与车辆匹配，抬起雨刮臂时在前挡风玻璃上垫一块软布，防止雨刮臂回弹损伤玻璃。

## 更换步骤

1. 将雨刮臂抬起并保持稳定。
2. 按下旧雨刮片连接处的释放卡扣，沿雨刮臂方向滑出旧雨刮片。
3. 将新雨刮片的接口对准雨刮臂挂钩，推入至听到卡扣锁定声。
4. 轻放雨刮臂，喷洒玻璃水后试刮，确认没有跳动、异响或刮不净。

## 注意事项

如果雨刮臂变形、雨刮电机异常或更换后仍刮不干净，应预约售后检查，不建议自行拆解雨刮电机。""",
    ),
    DocumentSeed(
        title="P0测试-车辆保养与保险说明",
        access_label="VISITOR",
        markdown="""# 车辆保养与保险说明

## 常规保养

保养周期应以车辆用户手册、仪表保养提示和实际使用工况为准。日常应检查轮胎胎压、制动液、玻璃水和轮胎磨损；长途出行前建议检查灯光、雨刮器和随车工具。

## 保险到期

保险到期日期以保单和车主账户中的车辆记录为准。保险临近到期时，应通过官方渠道核对保障范围和续保金额；不要仅根据历史保单推断当前保障状态。

## 年检

年检日期和要求受车辆注册日期、车辆类型及当地规则影响。车主应以车辆记录和当地车管部门要求为准，并提前准备行驶证、有效保险等材料。""",
    ),
    DocumentSeed(
        title="P0测试-员工售后工单处理规范",
        access_label="STAFF",
        markdown="""# 员工售后工单处理规范

## 首次响应

员工收到售后咨询后，应记录客户描述、车型、故障现象、发生时间和是否存在安全风险。涉及制动、转向、高压系统或人身安全的情况，应优先建议客户停止危险操作并联系官方救援或门店。

## 工单信息完整性

创建工单前应确认客户授权、联系方式和车辆归属。不得将其他客户的车辆识别号、订单、维修记录或联系方式写入当前工单。

## 升级处理

客户明确要求进店维修、预约、查询维修进度或对服务表达投诉时，应按售后流程升级处理并保留沟通记录。""",
    ),
)


async def seed_documents(settings: Settings, repository: EvaluationSeedRepository, *, apply: bool) -> list[str]:
    """按审核后向量化的真实链路写入缺失的测试知识文档。"""
    document_repository = KnowledgeDocumentRepository(settings)
    segment_repository = KnowledgeSegmentRepository(settings)
    vectorization_service = DocumentVectorizationService(
        document_repository,
        segment_repository,
        ElasticsearchEmbeddingStore(settings, embedder=DashScopeEmbeddingClient(settings)),
        RedisDistributedLock(settings),
    )
    review_service = DocumentReviewService(document_repository, vectorization_service)
    reviewer = SessionPrincipal(
        principal_type="STAFF", principal_id="LOCAL-EVALUATION-SEED", job="leader"
    )
    splitter = MarkdownParentChildSplitter()
    messages: list[str] = []
    for seed in _DOCUMENTS:
        existing = await repository.document_by_title(seed.title)
        if existing is not None:
            document_id, status = existing
            if apply and status.upper() in {"PENDING_REVIEW", "CHUNKED"}:
                await review_service.approve(reviewer, document_id, seed.access_label)  # type: ignore[arg-type]
                messages.append(f"{seed.title}：已续跑审核和向量化，文档 ID={document_id}")
            else:
                messages.append(f"{seed.title}：已存在（{status}），跳过")
            continue
        if not apply:
            messages.append(f"{seed.title}：将创建并向量化（{seed.access_label}）")
            continue
        document_id = await document_repository.create_uploaded_document(
            seed.title, "local-evaluation-seed"
        )
        source_url = f"seed://local-evaluation/{document_id}"
        await document_repository.complete_document(document_id, source_url, source_url)
        version_id = await document_repository.get_current_version_id(document_id)
        chunks = splitter.split(
            seed.markdown,
            document_id=document_id,
            filename=f"{seed.title}.md",
            source_url=source_url,
        )
        await segment_repository.replace_for_document(document_id, version_id, chunks)
        await review_service.approve(reviewer, document_id, seed.access_label)  # type: ignore[arg-type]
        messages.append(f"{seed.title}：已创建，文档 ID={document_id}")
    return messages


async def main() -> None:
    """解析参数、校验目标顾客，并按预览或写入模式初始化评测数据。"""
    parser = argparse.ArgumentParser(description="初始化本地 RAG 评测数据")
    parser.add_argument("--customer-phone", required=True, help="要绑定测试车的活动顾客手机号")
    parser.add_argument("--apply", action="store_true", help="确认写入并向量化测试数据")
    args = parser.parse_args()

    settings = Settings()
    repository = EvaluationSeedRepository(settings)
    customer_id = await repository.active_customer_id(args.customer_phone.strip())
    if customer_id is None:
        raise SystemExit("未找到活动顾客，未写入任何测试数据")
    mode = "写入" if args.apply else "预览"
    print(f"模式：{mode}；目标顾客 ID：{customer_id}")
    print(await repository.seed_vehicle(customer_id, apply=args.apply))
    for message in await seed_documents(settings, repository, apply=args.apply):
        print(message)
    if not args.apply:
        print("预览完成；确认后追加 --apply 执行写入和向量化。")


if __name__ == "__main__":
    asyncio.run(main())
