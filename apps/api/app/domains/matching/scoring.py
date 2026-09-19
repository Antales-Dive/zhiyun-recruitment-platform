"""匹配域：确定性评分与分流（版本化规则，AI 不改变权重与阈值）。

【为什么算分必须在代码里而不在模型里】
模型输出具有随机性，而"候选人是否进入面试"是需要复核、需要申诉入口的决定。
把权重与阈值收敛到 RubricConfig（版本化、可回滚、可 diff），使得：
  - 同一批 Agent 证据永远得到同一总分（可重放）；
  - 规则变更表现为一次显式的 rubric_version 变更，而非隐式提示词漂移；
  - 评估集 tests/evaluation/matching 能区分"模型变差"与"规则变严"。
本模块因此不 import 任何模型/网络依赖，是纯函数 + 纯规则，可直接单测覆盖边界。

【分数语义约定】
Agent 的 raw_score ∈ [0,100] 是"该维度的原始观察值"；
乘以 Rubric 权重后才是贡献分，总分同样落在 0-100 区间，与阈值同量纲。
"""
from dataclasses import dataclass

from app.contracts.matching import AgentResult, RubricConfig

# 分流去向：前四种是业务状态（会写进 candidate.status），
# NEEDS_REVIEW 是质量闸门状态，表示"这个分数不可信"，不是分数低。
ROUTE_INTERVIEW = "INTERVIEW"
ROUTE_QUESTIONNAIRE = "QUESTIONNAIRE"
ROUTE_TALENT_POOL = "TALENT_POOL"
ROUTE_CLOSED = "CLOSED"
ROUTE_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True)
class ScoreResult:
    """一次算分的完整产物。frozen：算完即不可改，任何改写路由都必须显式重算。

    score      加权总分（0-100）。
    confidence 各维度置信度的算术平均，用于置信度门。
    breakdown  每个维度的贡献分（= raw_score * weight），前端展示与人工复核的依据。
    route      分流去向，见 ROUTE_* 常量。
    """

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
    """旧接口兼容：40/40/20 权重确定性加权（规则边界测试基线）。

    与 DeterministicScorer 的区别：入参是三个裸分数而非 Agent 证据，
    confidence 固定 1.0（无 Agent 自评置信度可依据），因此永不触发 NEEDS_REVIEW。
    保留它是为了让"阈值边界 80/60/40"这组规则测试在引入 LangGraph 之后仍然可跑。
    新链路（graph.py）不要调用此函数。
    """
    weights = weights or {"skills": 0.4, "experience": 0.4, "completeness": 0.2}
    inputs = {"skill": skill_score, "experience": experience_score, "completeness": completeness_score}
    # 入参先夹区间：越界分数会让"总分是否等于 100"这类断言失去意义，直接拒绝而非截断。
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
    """80/60/40 四级分流；边界值进入高一级流程。

    用 `>=` 而非 `>`，即 80 分进面试、60 分进问卷、40 分进人才库：
    边界归属规则必须在代码里写死并测试覆盖，否则同分候选人的去向不可复现。
    """
    if score >= 80:
        return ROUTE_INTERVIEW
    if score >= 60:
        return ROUTE_QUESTIONNAIRE
    if score >= 40:
        return ROUTE_TALENT_POOL
    return ROUTE_CLOSED


class DeterministicScorer:
    """按版本化 Rubric 计算总分并分流；权重与阈值全部来自规则版本。

    无副作用、无 IO、无随机：给定 (rubric, agent_results) 必然得到同一 ScoreResult，
    因此可以只凭落库的 AgentRun 证据复现历史结论。
    """

    def __init__(self, rubric: RubricConfig):
        self.rubric = rubric

    def score(self, agent_results: dict[str, AgentResult]) -> ScoreResult:
        """加权求和 → 阈值分流 → 置信度门覆写。

        Raises:
            KeyError: rubric.dimensions 里有 agent_results 缺失的维度。
                正常不会发生——graph 的 supervisor_validate 已先校验三路齐全；
                若真发生说明调用绕过了 Supervisor，异常上抛由 run_matching 归为 GRAPH_FAILED，
                这里刻意不加 .get() 默认值把配置错误静默成 0 分。
        """
        breakdown: dict[str, float] = {}
        confidences: list[float] = []
        for dimension, weight in self.rubric.dimensions.items():
            result = agent_results[dimension]
            breakdown[dimension] = result.raw_score * weight
            confidences.append(result.confidence)
        total = sum(breakdown.values())
        # 总体置信度取算术平均：等权看待三路，避免某一路高置信掩盖另一路的证据缺失。
        # 空 dimensions 理论不可能（RubricConfig 校验权重非空），仍留 0.0 兜底以防除零。
        overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        thresholds = self.rubric.thresholds
        # 阈值来自规则版本而非常量，且 RubricConfig 已校验 interview > questionnaire > talent_pool，
        # 因此这里无需重复排序检查，直接按降序比较。
        if total >= thresholds["interview"]:
            route = ROUTE_INTERVIEW
        elif total >= thresholds["questionnaire"]:
            route = ROUTE_QUESTIONNAIRE
        elif total >= thresholds["talent_pool"]:
            route = ROUTE_TALENT_POOL
        else:
            route = ROUTE_CLOSED

        # 置信度门优先于分数：分数再高，只要证据不可信就一律转人工。
        # 覆写而非并列，是为了让"candidate.status 是否可自动流转"只有一个判定来源。
        if overall_confidence < self.rubric.confidence_threshold:
            route = ROUTE_REVIEW
        return ScoreResult(score=total, confidence=overall_confidence, breakdown=breakdown, route=route)

