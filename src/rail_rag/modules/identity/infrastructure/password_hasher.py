"""密码哈希服务：默认使用 Argon2id，并在受控过渡期校验旧 PBKDF2 摘要。

定义 PasswordHasher；hash 生成 Argon2id 摘要，verify 校验 Argon2id 或旧 PBKDF2 摘要，
needs_rehash 标记成功登录后应升级的旧摘要。明文、未知算法和格式错误一律拒绝。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


class PasswordHasher:
    """使用 Argon2id 保存密码，并兼容验证待迁移的 PBKDF2-HMAC-SHA256 摘要。"""

    _ALGORITHM = "pbkdf2_sha256"
    _HASH_NAME = "sha256"
    _SALT_BYTES = 16
    _KEY_BYTES = 32

    def __init__(
        self,
        legacy_pbkdf2_iterations: int = 600000,
        *,
        argon2_memory_cost_kib: int = 19456,
        argon2_time_cost: int = 2,
        argon2_parallelism: int = 1,
    ) -> None:
        self._legacy_pbkdf2_iterations = legacy_pbkdf2_iterations
        self._argon2 = Argon2PasswordHasher(
            time_cost=argon2_time_cost,
            memory_cost=argon2_memory_cost_kib,
            parallelism=argon2_parallelism,
            hash_len=self._KEY_BYTES,
            salt_len=self._SALT_BYTES,
        )

    def hash(self, password: str) -> str:
        """为非空密码生成可存入 VARCHAR(128) 的 Argon2id 摘要字符串。"""
        if not password:
            raise ValueError("Password must not be empty")
        return self._argon2.hash(password)

    def hash_legacy_pbkdf2(self, password: str) -> str:
        """仅供迁移测试生成旧 PBKDF2 摘要；业务代码不得再调用此方法保存新密码。"""
        normalized = password.encode("utf-8")
        if not normalized:
            raise ValueError("Password must not be empty")
        salt = secrets.token_bytes(self._SALT_BYTES)
        digest = hashlib.pbkdf2_hmac(
            self._HASH_NAME,
            normalized,
            salt,
            self._legacy_pbkdf2_iterations,
            self._KEY_BYTES,
        )
        return "$".join(
            (
                self._ALGORITHM,
                str(self._legacy_pbkdf2_iterations),
                _encode(salt),
                _encode(digest),
            )
        )

    def verify(self, password: str, encoded_hash: str | None) -> bool:
        """校验 Argon2id 或旧 PBKDF2 摘要；格式错误和明文值一律拒绝。"""
        if not password or not encoded_hash:
            return False
        if encoded_hash.startswith("$argon2id$"):
            try:
                return self._argon2.verify(encoded_hash, password)
            except (InvalidHashError, VerificationError):
                return False
        if not encoded_hash.startswith(f"{self._ALGORITHM}$"):
            return False
        try:
            algorithm, iterations, encoded_salt, expected = encoded_hash.split("$", 3)
            if algorithm != self._ALGORITHM:
                return False
            salt = _decode(encoded_salt)
            expected_bytes = _decode(expected)
            rounds = int(iterations)
            if (
                rounds < 100000
                or len(salt) != self._SALT_BYTES
                or len(expected_bytes) != self._KEY_BYTES
            ):
                return False
            actual = hashlib.pbkdf2_hmac(
                self._HASH_NAME,
                password.encode("utf-8"),
                salt,
                rounds,
                len(expected_bytes),
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected_bytes)

    def needs_rehash(self, encoded_hash: str | None) -> bool:
        """指出已成功验证的摘要是否必须更新为当前 Argon2id 参数。"""
        if not encoded_hash:
            return True
        if encoded_hash.startswith("$argon2id$"):
            try:
                return self._argon2.check_needs_rehash(encoded_hash)
            except (InvalidHashError, VerificationError):
                return True
        return True


def _encode(value: bytes) -> str:
    """使用无填充 URL 安全 Base64 编码二进制哈希数据。"""
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    """还原无填充 URL 安全 Base64 数据。"""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
