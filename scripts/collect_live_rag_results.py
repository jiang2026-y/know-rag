"""真实链路评测采集脚本：逐条运行问答编排并写出 RAGAS 运行结果。

定义 load_samples、build_principal、build_eval_row、collect_sample、check_prerequisites、
load_completed_keys、cleanup_conversations 和 main；脚本在进程内调用 ChatAnswerService，
不启动 FastAPI、不修改业务配置，单条样本失败不会中断整批评测。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from typing import Any
from uuid import uuid4

import httpx
import pymysql

from rail_rag.bootstrap import (
    get_chat_answer_service,
    get_chat_conversation_repository,
    get_settings,
)
from rail_rag.config.settings import Settings
from rail_rag.modules.conversation.application.chat_answer_service import ChatAnswerService
from rail_rag.modules.identity.domain.principal import (
    SessionPrincipal,
    conversation_owner_id,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    trust_env_for_host,
)
from rail_rag.modules.qa.infrastructure.dashscope_rerank_client import DashScopeRerankClient


logger = logging.getLogger("rail_rag.eval.collect")

EVAL_CONVERSATION_PREFIX = "eval-"
EVAL_TITLE_PREFIX = "[eval] "
EVAL_CAR_ID = "EVAL-CAR-001"
COMPLETED_STATUSES = frozenset({"OK", "SKIPPED"})


def load_samples(path: Path) -> list[dict[str, Any]]:
    """读取脱敏 JSONL 评测样本并校验必要字段。"""
    samples: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise ValueError(f"第 {line_number} 行缺少字符串 id")
        for field in ("question", "principal_type", "expected_related"):
            if field not in value:
                raise ValueError(f"{value['id']} 缺少字段 {field}")
        samples.append(value)
    return samples


def build_principal(
    sample: dict[str, Any],
    *,
    customer_id: str,
    staff_id: str,
    staff_job: str = "staff",
) -> SessionPrincipal:
    """按样本声明的身份类型构造可信主体，员工使用合成工号。"""
    principal_type = str(sample.get("principal_type", "")).strip().upper()
    if principal_type == "CUSTOMER":
        return SessionPrincipal(principal_type="CUSTOMER", principal_id=customer_id)
    if principal_type == "STAFF":
        return SessionPrincipal(
            principal_type="STAFF", principal_id=staff_id, job=staff_job
        )
    raise ValueError(f"{sample.get('id')} 的 principal_type 不受支持：{principal_type}")


def build_eval_row(
    sample: dict[str, Any],
    *,
    mode: str,
    response: str,
    contexts: list[str],
    sources: list[str],
    latency_ms: float,
    status: str,
    error: str | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """组装与 run_ragas_baseline.py 兼容的单条运行结果。"""
    return {
        "id": sample["id"],
        "mode": mode,
        "retrieved_contexts": contexts,
        "response": response,
        "sources": sources,
        "latency_ms": latency_ms,
        "status": status,
        "error": error,
        "metadata": metadata,
    }


async def collect_sample(
    sample: dict[str, Any],
    *,
    service: ChatAnswerService,
    repository: Any,
    principal: SessionPrincipal,
    mode: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """为单条样本创建独立会话，执行真实检索与回答并返回评测行。"""
    started = time.perf_counter()
    conversation_id = f"{EVAL_CONVERSATION_PREFIX}{uuid4()}"
    message_id = str(uuid4())
    await repository.create_with_first_message(
        conversation_id,
        conversation_owner_id(principal),
        f"{EVAL_TITLE_PREFIX}{sample['question'][:20]}",
        message_id,
        sample["question"],
    )
    prepared = await service.prepare(
        principal, conversation_id, message_id=message_id
    )
    answer_parts: list[str] = []
    citation_count = 0
    stream_error: str | None = None
    async for event in service.stream(prepared, f"eval-{uuid4()}"):
        if event is None:
            continue
        if event.event == "answer_delta":
            answer_parts.append(str(event.payload.get("delta", "")))
        elif event.event == "citation":
            citation_count += 1
        elif event.event == "error":
            stream_error = str(event.payload.get("message") or "模型流式回答失败")
    response = "".join(answer_parts).strip()
    related = bool(sample.get("expected_related", True))
    if stream_error is not None:
        status, response, error = "ERROR", "", stream_error
    elif not related:
        status, error = "SKIPPED", None
    elif not response:
        status, error = "ERROR", "模型返回空回答"
    else:
        status, error = "OK", None
    contexts = [
        item.text for item in prepared.evidence if item.text and item.text.strip()
    ]
    sources = sorted({item.source_type for item in prepared.evidence})
    route = prepared.route
    metadata = {
        "conversation_id": conversation_id,
        "intent": getattr(prepared.intent, "intent", None),
        "intent_related": bool(prepared.intent.related),
        "strategies": list(getattr(route, "strategies", []) or []),
        "route_confidence": getattr(route, "confidence", None),
        "rerank_status": prepared.rerank_status,
        "evidence_count": len(prepared.evidence),
        "context_chars": sum(len(text) for text in contexts),
        "answer_chars": len(response),
        "citation_count": citation_count,
        "history_count": len(prepared.history),
        "timeout_seconds": timeout_seconds,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    return build_eval_row(
        sample,
        mode=mode,
        response=response,
        contexts=contexts,
        sources=sources,
        latency_ms=round((time.perf_counter() - started) * 1000, 3),
        status=status,
        error=error,
        metadata=metadata,
    )


async def check_prerequisites(
    settings: Settings, *, customer_id: str
) -> list[tuple[str, bool, str]]:
    """只读检查数据库、检索服务、重排与模型配置，不调用任何模型。"""
    checks: list[tuple[str, bool, str]] = []

    has_key = (
        settings.embedding_api_key is not None
        and bool(settings.embedding_api_key.get_secret_value().strip())
    )
    checks.append(("百炼 API Key", has_key, "EMBEDDING_API_KEY 已配置" if has_key else "EMBEDDING_API_KEY 为空"))

    rerank_configured = DashScopeRerankClient(settings).configured
    checks.append(
        (
            "qwen3-rerank 配置",
            rerank_configured,
            f"{settings.chat_rerank_model_name} 已配置" if rerank_configured else "CHAT_RERANK_BASE_URL 未配置",
        )
    )

    mysql_detail = "已连接"
    mysql_ok = True
    try:
        await KnowledgeDocumentRepository(settings).check_connection()
        with pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password.get_secret_value(),
            database=settings.mysql_database,
            charset="utf8mb4",
            connect_timeout=settings.mysql_connect_timeout_seconds,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM knowledge_document "
                    "WHERE doc_title LIKE 'P0%' AND status='VECTOR_STORED' AND deleted=0"
                )
                document_count = int(cursor.fetchone()[0])
                cursor.execute("SELECT COUNT(*) FROM knowledge_segment")
                segment_count = int(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT user_id FROM my_car WHERE car_id=%s", (EVAL_CAR_ID,)
                )
                row = cursor.fetchone()
        if document_count < 3:
            mysql_ok = False
            mysql_detail = f"P0 文档仅 {document_count} 篇处于 VECTOR_STORED"
        elif segment_count < 1:
            mysql_ok = False
            mysql_detail = "knowledge_segment 中没有分片"
        elif row is None or str(row[0]) != str(customer_id):
            mysql_ok = False
            mysql_detail = f"{EVAL_CAR_ID} 未绑定到顾客 {customer_id}"
        else:
            mysql_detail = f"P0 文档 {document_count} 篇、分片 {segment_count} 条，测试车绑定正确"
    except Exception as exc:  # 连接失败时给出可读原因，不中断其余检查
        mysql_ok = False
        mysql_detail = f"{type(exc).__name__}: {exc}"
    checks.append(("MySQL 评测数据", mysql_ok, mysql_detail))

    elasticsearch_ok = False
    elasticsearch_detail = ""
    try:
        async with httpx.AsyncClient(
            base_url=str(settings.elasticsearch_host).rstrip("/"),
            timeout=5,
            trust_env=trust_env_for_host(str(settings.elasticsearch_host)),
        ) as client:
            health = await client.get("/_cluster/health")
            health.raise_for_status()
            status = str(health.json().get("status", ""))
            analyzer = await client.post(
                "/_analyze",
                json={
                    "analyzer": settings.elasticsearch_text_analyzer,
                    "text": "保险到期",
                },
            )
            index = await client.head(f"/{settings.elasticsearch_index}")
        if status not in {"green", "yellow"}:
            elasticsearch_detail = f"集群状态为 {status or 'unknown'}"
        elif analyzer.status_code == 400:
            elasticsearch_detail = (
                f"分析器 {settings.elasticsearch_text_analyzer} 不可用；"
                "未安装 IK 时请设置 ELASTICSEARCH_TEXT_ANALYZER=standard"
            )
        elif index.status_code == 404:
            elasticsearch_detail = (
                f"索引 {settings.elasticsearch_index} 不存在，"
                "请先运行 scripts/revectorize_eval_documents.py --apply"
            )
        else:
            elasticsearch_ok = True
            elasticsearch_detail = (
                f"集群 {status}，分析器 {settings.elasticsearch_text_analyzer} 可用，"
                f"索引 {settings.elasticsearch_index} 存在"
            )
    except Exception as exc:
        elasticsearch_detail = f"{type(exc).__name__}: {exc}"
    checks.append(("Elasticsearch", elasticsearch_ok, elasticsearch_detail))

    neo4j_ok = False
    neo4j_detail = ""
    if not settings.neo4j_enabled:
        neo4j_detail = "NEO4J_ENABLED 未开启，图数据库样本会退化为知识库检索"
    else:
        driver = None
        try:
            from neo4j import AsyncGraphDatabase

            driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(
                    settings.neo4j_user,
                    settings.neo4j_password.get_secret_value()
                    if settings.neo4j_password is not None
                    else "",
                ),
                connection_timeout=settings.neo4j_connect_timeout_seconds,
            )
            await driver.verify_connectivity()
            async with driver.session(database=settings.neo4j_database) as session:
                result = await session.run("MATCH (b:Brand) RETURN count(b) AS total")
                record = await result.single()
            total = int(record["total"]) if record is not None else 0
            if total < 1:
                neo4j_detail = "图谱中没有 Brand 节点，请先加载 public_vehicle_graph.cypher"
            else:
                neo4j_ok = True
                neo4j_detail = f"已连接，Brand 节点 {total} 个"
        except Exception as exc:
            neo4j_detail = f"{type(exc).__name__}: {exc}"
        finally:
            if driver is not None:
                await driver.close()
    checks.append(("Neo4j 公开图谱", neo4j_ok, neo4j_detail))
    return checks


def load_completed_keys(path: Path) -> set[str]:
    """读取既有结果，返回已完成样本的 id::mode 集合，用于断点续跑。"""
    if not path.exists():
        return set()
    completed: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and row.get("status") in COMPLETED_STATUSES
        ):
            completed.add(f"{row['id']}::{row.get('mode', 'current')}")
    return completed


def cleanup_conversations(settings: Settings, conversation_ids: list[str]) -> int:
    """删除本次评测生成的会话与消息，返回清理的会话数。"""
    if not conversation_ids:
        return 0
    placeholders = ", ".join(["%s"] * len(conversation_ids))
    with pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password.get_secret_value(),
        database=settings.mysql_database,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=settings.mysql_connect_timeout_seconds,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM chat_message WHERE conversation_id IN ({placeholders})",
                tuple(conversation_ids),
            )
            cursor.execute(
                f"DELETE FROM chat_conversation WHERE conversation_id IN ({placeholders})",
                tuple(conversation_ids),
            )
        connection.commit()
    return len(conversation_ids)


def append_row(path: Path, row: dict[str, Any]) -> None:
    """以 UTF-8 追加一行结果，确保中途失败也保留已完成样本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run(args: argparse.Namespace) -> int:
    """执行前置检查或逐条采集，返回进程退出码。"""
    settings = get_settings()
    if args.dry_run:
        checks = await check_prerequisites(settings, customer_id=args.customer_id)
        for label, ok, detail in checks:
            print(f"{'[OK]' if ok else '[FAIL]'} {label}：{detail}")
        failed = [label for label, ok, _ in checks if not ok]
        if failed:
            print(f"前置检查未通过：{', '.join(failed)}")
            return 1
        print("前置检查全部通过，可以执行真实链路采集。")
        return 0

    samples = load_samples(args.dataset)
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit 必须是正整数")
        samples = samples[: args.limit]
    completed = load_completed_keys(args.output) if args.resume else set()
    if not args.resume and args.output.exists():
        args.output.unlink()

    service = get_chat_answer_service()
    repository = get_chat_conversation_repository()
    created_conversations: list[str] = []
    counts = {"OK": 0, "SKIPPED": 0, "ERROR": 0}
    for index, sample in enumerate(samples, start=1):
        key = f"{sample['id']}::{args.mode}"
        if key in completed:
            print(f"[{index}/{len(samples)}] 跳过已完成样本 {sample['id']}")
            continue
        principal = build_principal(
            sample,
            customer_id=args.customer_id,
            staff_id=args.staff_id,
            staff_job=args.staff_job,
        )
        started = time.perf_counter()
        try:
            row = await asyncio.wait_for(
                collect_sample(
                    sample,
                    service=service,
                    repository=repository,
                    principal=principal,
                    mode=args.mode,
                    timeout_seconds=args.timeout_seconds,
                ),
                timeout=args.timeout_seconds,
            )
        except asyncio.TimeoutError:
            row = build_eval_row(
                sample,
                mode=args.mode,
                response="",
                contexts=[],
                sources=[],
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                status="ERROR",
                error=f"采集超时（>{args.timeout_seconds}s）",
                metadata={"principal_type": sample.get("principal_type")},
            )
        except Exception as exc:
            row = build_eval_row(
                sample,
                mode=args.mode,
                response="",
                contexts=[],
                sources=[],
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                status="ERROR",
                error=f"{type(exc).__name__}: {exc}",
                metadata={"principal_type": sample.get("principal_type")},
            )
        conversation_id = row.get("metadata", {}).get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.startswith(
            EVAL_CONVERSATION_PREFIX
        ):
            created_conversations.append(conversation_id)
        append_row(args.output, row)
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        print(
            f"[{index}/{len(samples)}] {sample['id']} -> {row['status']} "
            f"（证据 {len(row['retrieved_contexts'])} 条，{row['latency_ms']} ms）"
        )

    if args.cleanup:
        removed = cleanup_conversations(settings, created_conversations)
        print(f"已清理本次生成的评测会话：{removed} 个")
    print(
        f"采集完成：OK={counts.get('OK', 0)} SKIPPED={counts.get('SKIPPED', 0)} "
        f"ERROR={counts.get('ERROR', 0)}；结果：{args.output}"
    )
    return 0 if counts.get("ERROR", 0) == 0 else 2


def main() -> None:
    """解析参数并启动真实链路采集。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="采集真实链路 RAG 结果用于 RAGAS 评分")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=root / "evals" / "datasets" / "p0_baseline.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "evals" / "reports" / "live" / "runtime-results.jsonl",
    )
    parser.add_argument("--mode", choices=["baseline", "current"], default="current")
    parser.add_argument("--limit", type=int, help="仅采集前 N 条样本")
    parser.add_argument("--customer-id", default="1", help="CUSTOMER 样本使用的顾客 ID")
    parser.add_argument("--staff-id", default="EVAL-STAFF", help="STAFF 样本使用的合成工号")
    parser.add_argument("--staff-job", choices=["staff", "leader"], default="staff")
    parser.add_argument("--timeout-seconds", type=float, default=240.0, help="单条样本采集超时")
    parser.add_argument("--resume", action="store_true", help="跳过输出文件中已完成的样本")
    parser.add_argument("--cleanup", action="store_true", help="采集结束后删除本次生成的评测会话")
    parser.add_argument("--dry-run", action="store_true", help="只做前置检查，不调用模型")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
