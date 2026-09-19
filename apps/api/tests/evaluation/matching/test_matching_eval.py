"""匹配评估集：确定性评分与分流基线的可复现案例。

每条案例记录输入分数组合、期望分流与规则版本；评估只验证确定性
规则（不依赖 Provider），AI 证据提取质量在 Provider 就绪后另建评估。
"""
import json
from pathlib import Path

import pytest
from app.contracts.matching import RubricConfig
from app.domains.matching.scoring import DeterministicScorer

DATASET_VERSION = "2026-09-13-v1"
DATASET_PATH = Path(__file__).parent / "matching_eval_cases.jsonl"


def load_cases() -> list[dict]:
    cases = []
    with open(DATASET_PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


CASES = load_cases()


def make_results(scores: dict[str, int], confidence: float = 0.9):
    from app.contracts.matching import AgentResult

    return {
        dim: AgentResult(
            dimension=dim,
            raw_score=score,
            confidence=confidence,
            evidence_refs=["b1"],
            missing_fields=[],
            reason_codes=[],
        )
        for dim, score in scores.items()
    }


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_evaluation_case(case):
    rubric = RubricConfig(
        dimensions=case["rubric"]["dimensions"],
        thresholds=case["rubric"]["thresholds"],
        confidence_threshold=case["rubric"]["confidence_threshold"],
    )
    results = make_results(case["agent_scores"], confidence=case.get("confidence", 0.9))

    scored = DeterministicScorer(rubric).score(results)

    assert scored.route == case["expected_route"], (
        f"{case['case_id']}: 期望 {case['expected_route']}，实际 {scored.route}（score={scored.score:.1f}）"
    )
    if case.get("expected_score"):
        assert abs(scored.score - case["expected_score"]) < 1e-6


def test_dataset_metadata_is_versioned():
    assert DATASET_VERSION.startswith("2026-09-13")
    assert CASES, "评估集不能为空"
