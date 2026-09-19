"""身份模块：密码散列（不可逆存储）单元测试。"""
from app.domains.identity.password import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    digest = hash_password("correct-horse-42")
    assert verify_password("correct-horse-42", digest)


def test_wrong_password_is_rejected():
    digest = hash_password("correct-horse-42")
    assert not verify_password("wrong-password", digest)


def test_same_password_produces_distinct_hashes():
    assert hash_password("same") != hash_password("same")


def test_malformed_hash_is_rejected_safely():
    assert not verify_password("anything", "not-a-hash")
    assert not verify_password("anything", "scrypt$1$1$1$too-short")


def test_hash_format_is_self_describing():
    digest = hash_password("x")
    parts = digest.split("$")
    assert parts[0] == "scrypt"
    assert len(parts) == 6  # algorithm, n, r, p, salt, hash
    assert parts[1] == "16384"
