"""验证 Argon2id 主密码哈希、PBKDF2 过渡校验和错误格式拒绝行为。"""

from rail_rag.modules.identity.infrastructure.password_hasher import PasswordHasher


def test_hash_verifies_only_the_original_password() -> None:
    """同一密码可通过 Argon2id 校验，错误密码和明文存储值都会失败。"""
    hasher = PasswordHasher(
        legacy_pbkdf2_iterations=100000,
        argon2_memory_cost_kib=8192,
        argon2_time_cost=1,
    )

    encoded = hasher.hash("correct horse battery staple")

    assert encoded.startswith("$argon2id$v=19$m=8192,t=1,p=1$")
    assert hasher.verify("correct horse battery staple", encoded) is True
    assert hasher.verify("incorrect", encoded) is False
    assert hasher.verify("123456", "123456") is False
    assert hasher.needs_rehash(encoded) is False


def test_legacy_pbkdf2_hash_verifies_and_requires_upgrade() -> None:
    """旧 PBKDF2 摘要仍可过渡登录，但必须标记为 Argon2id 升级对象。"""
    hasher = PasswordHasher(
        legacy_pbkdf2_iterations=100000,
        argon2_memory_cost_kib=8192,
        argon2_time_cost=1,
    )
    encoded = hasher.hash_legacy_pbkdf2("legacy-password")

    assert hasher.verify("legacy-password", encoded) is True
    assert hasher.needs_rehash(encoded) is True
