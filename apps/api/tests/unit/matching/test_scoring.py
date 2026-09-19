"""匹配单元测试：确定性评分、分流边界、置信度门与 Agent 输出校验。"""
import json

import pytest
from app.contracts.matching import AgentResult, RubricConfig
from app.domains.matching.agents import parse_agent_response
from app.domains.matching.scoring import (
    ROUTE_CLOSED,
    ROUTE_INTERVIEW,
    ROUTE_QUESTIONNAIRE,
    ROUTE_REVIEW,
    ROUTE_TALENT_POOL,
    DeterministicScorer,
    calculate_match_score,
    route_candidate,
)
from app.infrastructure.model_gateway import ProviderInvalidResponseError


def make_result(dimension, score, confidence=0.9, evidence=("b1",)):
    return AgentResult(
        dimension=dimension,
        raw_score=score,
        confidence=confidence,
        evidence_refs=list(evidence),
    )


class TestLegacyScoring:
    def test_weighted_score_uses_40_40_20(self):
        result = calculate_match_score(skill_score=90, experience_score=80, completeness_score=70)
        assert result.score == 82.0
        assert result.breakdown == {"skills": 36.0, "experience": 32.0, "completeness": 14.0}

    def test_rejects_out_of_range_scores(self):
        with pytest.raises(ValueError):
            calculate_match_score(skill_score=101, experience_score=80, completeness_score=70)

    def test_route_boundaries(self):
        assert route_candidate(80) == ROUTE_INTERVIEW
        assert route_candidate(60) == ROUTE_QUESTIONNAIRE
        assert route_candidate(40) == ROUTE_TALENT_POOL
        assert route_candidate(39.9) == ROUTE_CLOSED


class TestRubricConfig:
    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError):
            RubricConfig(dimensions={"basic": 0.5, "skills": 0.5, "experience": 0.5})

    def test_thresholds_must_be_ordered(self):
        with pytest.raises(ValueError):
            RubricConfig(thresholds={"interview": 60, "questionnaire": 80, "talent_pool": 40})


class TestDeterministicScorer:
    def test_score_applies_versioned_weights(self):
        rubric = RubricConfig(
            dimensions={"basic": 0.2, "skills": 0.4, "experience": 0.4},
            thresholds={"interview": 80, "questionnaire": 60, "talent_pool": 40},
        )
        results = {
            "basic": make_result("basic", 70),
            "skills": make_result("skills", 90),
            "experience": make_result("experience", 80),
        }

        scored = DeterministicScorer(rubric).score(results)

        assert scored.score == 82.0
        assert scored.route == ROUTE_INTERVIEW
        assert scored.breakdown == {"basic": 14.0, "skills": 36.0, "experience": 32.0}

    def test_boundary_routing_uses_rubric_thresholds(self):
        rubric = RubricConfig(
            dimensions={"basic": 0.2, "skills": 0.4, "experience": 0.4},
            thresholds={"interview": 80, "questionnaire": 60, "talent_pool": 40},
        )
        scorer = DeterministicScorer(rubric)
        cases = [
            ({"basic": 80, "skills": 80, "experience": 80}, ROUTE_INTERVIEW),
            ({"basic": 60, "skills": 60, "experience": 60}, ROUTE_QUESTIONNAIRE),
            ({"basic": 40, "skills": 40, "experience": 40}, ROUTE_TALENT_POOL),
            ({"basic": 30, "skills": 30, "experience": 30}, ROUTE_CLOSED),
        ]
        for scores, expected in cases:
            results = {
                dim: make_result(dim, score) for dim, score in scores.items()
            }
            assert scorer.score(results).route == expected

    def test_low_confidence_forces_review(self):
        rubric = RubricConfig(confidence_threshold=0.6)
        results = {
            "basic": make_result("basic", 90, confidence=0.3),
            "skills": make_result("skills", 90, confidence=0.3),
            "experience": make_result("experience", 90, confidence=0.3),
        }

        scored = DeterministicScorer(rubric).score(results)

        assert scored.route == ROUTE_REVIEW


class TestAgentOutputValidation:
    def test_parses_valid_structured_output(self):
        content = json.dumps(
            {
                "dimension": "skills",
                "raw_score": 85,
                "confidence": 0.9,
                "evidence_refs": ["b1", "b2"],
                "missing_fields": [],
                "reason_codes": ["SKILL_MATCH"],
            }
        )

        result = parse_agent_response(content, dimension="skills", valid_block_ids={"b1", "b2"})

        assert result.raw_score == 85
        assert result.evidence_refs == ["b1", "b2"]

    def test_rejects_markdown_wrapped_json(self):
        payload = {
            "dimension": "basic",
            "raw_score": 70,
            "confidence": 0.8,
            "evidence_refs": ["b1"],
            "missing_fields": [],
            "reason_codes": [],
        }
        content = "```json\n" + json.dumps(payload) + "\n```"

        result = parse_agent_response(content, dimension="basic", valid_block_ids={"b1"})

        assert result.raw_score == 70

    def test_rejects_invalid_evidence_refs(self):
        content = json.dumps(
            {
                "dimension": "skills",
                "raw_score": 85,
                "confidence": 0.9,
                "evidence_refs": ["ghost"],
                "missing_fields": [],
                "reason_codes": [],
            }
        )

        with pytest.raises(ProviderInvalidResponseError, match="证据引用不存在"):
            parse_agent_response(content, dimension="skills", valid_block_ids={"b1"})

    def test_rejects_dimension_mismatch(self):
        content = json.dumps(
            {
                "dimension": "basic",
                "raw_score": 85,
                "confidence": 0.9,
                "evidence_refs": ["b1"],
                "missing_fields": [],
                "reason_codes": [],
            }
        )

        with pytest.raises(ProviderInvalidResponseError, match="维度不符"):
            parse_agent_response(content, dimension="skills", valid_block_ids={"b1"})

    def test_rejects_malformed_json(self):
        with pytest.raises(ProviderInvalidResponseError):
            parse_agent_response("{not json", dimension="skills", valid_block_ids={"b1"})

    def test_rejects_score_out_of_range(self):
        content = json.dumps(
            {
                "dimension": "skills",
                "raw_score": 150,
                "confidence": 0.9,
                "evidence_refs": ["b1"],
                "missing_fields": [],
                "reason_codes": [],
            }
        )

        with pytest.raises(ProviderInvalidResponseError):
            parse_agent_response(content, dimension="skills", valid_block_ids={"b1"})
