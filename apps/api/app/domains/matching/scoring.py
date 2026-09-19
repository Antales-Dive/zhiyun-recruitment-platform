"""匹配域：确定性评分与分流（版本化规则，AI 不改变权重与阈值）。"""
from dataclasses import dataclass

from app.contracts.matching import AgentResult, RubricConfig

ROUTE_INTERVIEW = "INTERVIEW"
ROUTE_QUESTIONNAIRE = "QUESTIONNAIRE"
ROUTE_TALENT_POOL = "TALENT_POOL"
ROUTE_CLOSED = "CLOSED"
ROUTE_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True)
class ScoreResult:
    score: float
    confidence: float
    breakdown: dict[str, float]
    route: str


def calculate_match_score(
    skill_score: int,
    experience_score: int,
    completeness_score: int,
    weights: dict[str, float] | None = None,
) -> ScoreResult:
    """旧接口兼容：40/40/20 权重确定性加权（规则边界测试基线）。"""
    weights = weights or {"skills": 0.4, "experience": 0.4, "completeness": 0.2}
    inputs = {"skill": skill_score, "experience": experience_score, "completeness": completeness_score}
    for name, value in inputs.items():
        if not 0 <= value <= 100:
            raise ValueError(f"{name} 分数必须在 0-100 之间")
    breakdown = {
        "skills": skill_score * weights["skills"],
        "experience": experience_score * weights["experience"],
        "completeness": completeness_score * weights["completeness"],
    }
    total = sum(breakdown.values())
    return ScoreResult(
        score=total,
        confidence=1.0,
        breakdown=breakdown,
        route=route_candidate(total),
    )


def route_candidate(score: float) -> str:
    """80/60/40 四级分流；边界值进入高一级流程。"""
    if score >= 80:
        return ROUTE_INTERVIEW
    if score >= 60:
        return ROUTE_QUESTIONNAIRE
    if score >= 40:
        return ROUTE_TALENT_POOL
    return ROUTE_CLOSED


class DeterministicScorer:
    """按版本化 Rubric 计算总分并分流；权重与阈值全部来自规则版本。"""

    def __init__(self, rubric: RubricConfig):
        self.rubric = rubric

    def score(self, agent_results: dict[str, AgentResult]) -> ScoreResult:
        breakdown: dict[str, float] = {}
        confidences: list[float] = []
        for dimension, weight in self.rubric.dimensions.items():
            result = agent_results[dimension]
            breakdown[dimension] = result.raw_score * weight
            confidences.append(result.confidence)
        total = sum(breakdown.values())
        overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        thresholds = self.rubric.thresholds
        if total >= thresholds["interview"]:
            route = ROUTE_INTERVIEW
        elif total >= thresholds["questionnaire"]:
            route = ROUTE_QUESTIONNAIRE
        elif total >= thresholds["talent_pool"]:
            route = ROUTE_TALENT_POOL
        else:
            route = ROUTE_CLOSED

        if overall_confidence < self.rubric.confidence_threshold:
            route = ROUTE_REVIEW
        return ScoreResult(score=total, confidence=overall_confidence, breakdown=breakdown, route=route)
