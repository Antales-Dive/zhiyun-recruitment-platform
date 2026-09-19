"""事件 envelope 单元测试：schema 兼容边界与非法输入。"""
import json

import pytest
from app.infrastructure.messaging.envelope import (
    EnvelopeError,
    EventEnvelope,
    UnsupportedSchemaError,
    build_envelope,
    parse_envelope,
)


def sample_envelope(**overrides):
    envelope = build_envelope(
        event_id="evt-1",
        event_type="resume.parse.requested",
        aggregate_type="candidate",
        aggregate_id="cand-1",
        org_id="org-a",
        trace_id="trace-1",
        payload={"task_id": "t1"},
    )
    envelope.update(overrides)
    return envelope


class TestEnvelope:
    def test_build_and_parse_roundtrip(self):
        raw = json.dumps(sample_envelope())

        parsed = parse_envelope(raw)

        assert isinstance(parsed, EventEnvelope)
        assert parsed.event_id == "evt-1"
        assert parsed.event_type == "resume.parse.requested"
        assert parsed.payload == {"task_id": "t1"}

    def test_newer_major_schema_is_rejected(self):
        raw = json.dumps(sample_envelope(schema_version=2))

        with pytest.raises(UnsupportedSchemaError):
            parse_envelope(raw)

    def test_malformed_json_is_rejected(self):
        with pytest.raises(EnvelopeError):
            parse_envelope("{not json")

    def test_missing_required_fields_are_rejected(self):
        payload = sample_envelope()
        del payload["aggregate_id"]

        with pytest.raises(EnvelopeError):
            parse_envelope(json.dumps(payload))

    def test_same_major_version_is_accepted(self):
        raw = json.dumps(sample_envelope(schema_version=1))
        assert parse_envelope(raw).schema_version == 1
