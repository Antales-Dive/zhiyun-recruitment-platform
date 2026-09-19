"""审计模块：字段白名单与 PII 脱敏单元测试。"""
from app.domains.audit.redaction import MASK, redact_diff


def test_whitelist_drops_unlisted_fields():
    before = {"score": 80, "email": "a@example.com", "internal_note": "秘密"}
    after = {"score": 85, "email": "a@example.com"}

    redacted_before, redacted_after = redact_diff(before, after)

    assert "score" in redacted_before
    assert "internal_note" not in redacted_before
    assert redacted_after == {"score": 85, "email": MASK}


def test_pii_values_are_masked_not_deleted():
    before = {"status": "ACTIVE", "email": "a@example.com"}
    after = {"status": "INACTIVE", "email": "a@example.com"}

    _, redacted_after = redact_diff(before, after)

    assert redacted_after["email"] == MASK
    assert "a@example.com" not in str(redacted_after)


def test_token_and_password_never_survive_redaction():
    before = {"token": "raw-token", "password_hash": "digest", "status": "PENDING"}
    after = {"token": "raw-token", "status": "ACTIVE"}

    redacted_before, redacted_after = redact_diff(before, after)

    assert "token" not in redacted_before
    assert "password_hash" not in redacted_before
    assert "token" not in redacted_after
    assert "raw-token" not in str(redacted_after)
    assert "digest" not in str(redacted_after)


def test_none_values_are_supported():
    redacted_before, redacted_after = redact_diff(None, {"status": "ACTIVE"})
    assert redacted_before is None
    assert redacted_after == {"status": "ACTIVE"}


def test_nested_pii_keys_are_masked():
    before = {"decision": {"email": "a@example.com", "route": "INTERVIEW"}}
    _, redacted = redact_diff(before, before)
    assert redacted["decision"]["email"] == MASK
    assert redacted["decision"]["route"] == "INTERVIEW"
