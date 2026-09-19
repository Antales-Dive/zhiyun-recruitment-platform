"""匹配单元测试：LangGraph 三路图在 Provider 缺失/成功/失败下的行为。"""
import json
import re

from app.contracts.matching import AnalysisSnapshot, RubricConfig
from app.domains.matching.graph import run_matching
from app.domains.matching.scoring import ROUTE_INTERVIEW


def make_snapshot():
    return AnalysisSnapshot(
        job_version_id="jv1",
        rubric_version_id="rv1",
        resume_version_id="rv1",
        job_title="Python 工程师",
        job_description="后端开发",
        requirements=["Python", "FastAPI"],
        resume_blocks=[
            {"id": "b1", "section": "技能", "text": "Python FastAPI 5 年经验"},
            {"id": "b2", "section": "经历", "text": "负责后端服务开发"},
        ],
    )


class StubGateway:
    """测试用结构化输出桩：按维度返回合法 JSON，不走真实 Provider。"""

    def __init__(self, *, fail_dimension: str | None = None, invalid_ref: bool = False):
        self.fail_dimension = fail_dimension
        self.invalid_ref = invalid_ref

    def is_configured(self) -> bool:
        return True

    def chat(self, messages, *, timeout_seconds: int = 60):
        match = re.search(r"（(basic|skills|experience)）", messages[1]["content"])
        dimension = match.group(1) if match else "basic"
        if self.fail_dimension == dimension:
            raise ProviderUnavailableErrorStub()
        refs = ["b1"] if not self.invalid_ref else ["ghost"]
        payload = {
            "dimension": dimension,
            "raw_score": 85 if dimension != "basic" else 70,
            "confidence": 0.9,
            "evidence_refs": refs,
            "missing_fields": [],
            "reason_codes": [],
        }
        return StubChatResult(content=json.dumps(payload))


class StubChatResult:
    def __init__(self, content):
        self.content = content


class ProviderUnavailableErrorStub(Exception):
    pass


def test_provider_not_configured_enters_review_without_scores():
    state = run_matching(
        snapshot=make_snapshot(),
        rubric=RubricConfig(),
        valid_block_ids={"b1", "b2"},
        gateway=None,
    )

    assert state.needs_review is True
    assert state.review_reason == "MODEL_NOT_CONFIGURED"
    assert state.agent_results == {}
    assert state.score is None


def test_all_agents_succeed_and_score_computed():
    state = run_matching(
        snapshot=make_snapshot(),
        rubric=RubricConfig(
            dimensions={"basic": 0.2, "skills": 0.4, "experience": 0.4},
            thresholds={"interview": 80, "questionnaire": 60, "talent_pool": 40},
        ),
        valid_block_ids={"b1", "b2"},
        gateway=StubGateway(),
    )

    assert state.needs_review is False
    assert set(state.agent_results) == {"basic", "skills", "experience"}
    assert state.score is not None
    assert state.score.route == ROUTE_INTERVIEW


def test_agent_failure_enters_review_with_reason():
    state = run_matching(
        snapshot=make_snapshot(),
        rubric=RubricConfig(),
        valid_block_ids={"b1", "b2"},
        gateway=StubGateway(fail_dimension="skills"),
    )

    assert state.needs_review is True
    assert state.agent_errors
    assert state.score is None


def test_invalid_evidence_reference_enters_review():
    state = run_matching(
        snapshot=make_snapshot(),
        rubric=RubricConfig(),
        valid_block_ids={"b1", "b2"},
        gateway=StubGateway(invalid_ref=True),
    )

    assert state.needs_review is True
    assert state.review_reason is not None
