"""安全密码初始化工具：为指定顾客或员工交互式设置 Argon2id 密码并撤销旧 Session。

定义 main、_parse_arguments、_read_new_password、_find_account、_apply_reset 和
_revoke_sessions；默认只读预览，只有显式传入 --apply 才会更新 MySQL，密码不会进入命令行参数或日志。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
from typing import Literal

import pymysql

from rail_rag.config.settings import get_settings
from rail_rag.modules.identity.infrastructure.password_hasher import PasswordHasher
from rail_rag.modules.identity.infrastructure.session_service import RedisSessionService


AccountType = Literal["customer", "staff"]


def _parse_arguments() -> argparse.Namespace:
    """解析账号类型、账号标识和显式写入开关。"""
    parser = argparse.ArgumentParser(description="为测试账号设置 Argon2id 密码")
    parser.add_argument("account_type", choices=("customer", "staff"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--phone", help="顾客手机号")
    group.add_argument("--emp-id", help="员工工号")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="确认后写入 MySQL；不提供时只做账号状态预览",
    )
    args = parser.parse_args()
    if args.account_type == "customer" and not args.phone:
        parser.error("customer 必须使用 --phone")
    if args.account_type == "staff" and not args.emp_id:
        parser.error("staff 必须使用 --emp-id")
    return args


def _read_new_password() -> str:
    """从隐藏终端输入两次新密码，不接受空值或超过接口上限的密码。"""
    password = getpass.getpass("新密码（输入不可见）：")
    confirmation = getpass.getpass("再次输入新密码：")
    if not password or password != confirmation:
        raise ValueError("两次密码不一致或密码为空")
    if len(password) > 256:
        raise ValueError("密码长度不能超过 256 个字符")
    return password


def _connect() -> pymysql.Connection:
    """使用项目 .env 创建 MySQL 连接，不在日志中输出连接凭据。"""
    settings = get_settings()
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password.get_secret_value(),
        database=settings.mysql_database,
        charset="utf8mb4",
        connect_timeout=settings.mysql_connect_timeout_seconds,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _find_account(account_type: AccountType, identifier: str) -> dict[str, object] | None:
    """按服务端登录条件查询账号，只返回 ID 和状态，不读取旧密码。"""
    table = "user_info" if account_type == "customer" else "staff_info"
    column = "phone" if account_type == "customer" else "emp_id"
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id, {column} AS account_identifier, status FROM {table} WHERE {column} = %s LIMIT 1",
                (identifier.strip(),),
            )
            return cursor.fetchone()


def _revoke_sessions(account_type: AccountType, account_id: object) -> int:
    """在写入新密码前撤销主体全部 Session，失败时阻止密码更新。"""
    service = RedisSessionService(get_settings())
    if account_type == "customer":
        return asyncio.run(service.revoke_customer_sessions(str(account_id)))
    return asyncio.run(service.revoke_staff_sessions(str(account_id)))


def _apply_reset(account_type: AccountType, account: dict[str, object], password: str) -> int:
    """先撤销旧会话，再以条件更新密码哈希，避免更新失败时留下旧登录态。"""
    settings = get_settings()
    hasher = PasswordHasher(
        legacy_pbkdf2_iterations=settings.auth_password_hash_iterations,
        argon2_memory_cost_kib=settings.auth_argon2_memory_cost_kib,
        argon2_time_cost=settings.auth_argon2_time_cost,
        argon2_parallelism=settings.auth_argon2_parallelism,
    )
    new_hash = hasher.hash(password)
    account_id = account["id"]
    identifier_column = "phone" if account_type == "customer" else "emp_id"
    table = "user_info" if account_type == "customer" else "staff_info"
    status = "ACTIVE" if account_type == "customer" else "ON_JOB"
    identifier = str(account["account_identifier"])
    revoked_count = _revoke_sessions(account_type, account_id if account_type == "customer" else identifier)
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            affected = cursor.execute(
                f"UPDATE {table} SET password = %s WHERE {identifier_column} = %s AND status = %s",
                (new_hash, identifier, status),
            )
        if affected != 1:
            connection.rollback()
            raise RuntimeError("账号状态在操作期间发生变化，密码未更新")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return revoked_count


def main() -> int:
    """执行预览或显式确认的 Argon2id 密码初始化。"""
    args = _parse_arguments()
    identifier = (args.phone or args.emp_id).strip()
    account = _find_account(args.account_type, identifier)
    if account is None:
        print("未找到账号；未执行任何修改。")
        return 1
    expected_status = "ACTIVE" if args.account_type == "customer" else "ON_JOB"
    print(f"账号存在，当前状态：{account['status']}；要求登录状态：{expected_status}")
    if account["status"] != expected_status:
        print("账号当前不可登录；未执行任何修改。")
        return 1
    if not args.apply:
        print("预览模式：不会修改密码或撤销 Session。添加 --apply 才会继续。")
        return 0
    password = _read_new_password()
    revoked_count = _apply_reset(args.account_type, account, password)
    print(f"密码已更新为 Argon2id；已撤销 {revoked_count} 个旧 Session。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
