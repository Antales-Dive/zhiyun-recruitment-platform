"""Matching LangGraph：三路并行 Agent → Supervisor 校验 → 确定性评分。

流程（03-architecture.md §5.2）：
  load_snapshot -> fan_out(basic/skill/experience) -> supervisor_validate
  -> deterministic_score -> confidence_gate -> route_or_human_review -> persist

并行节点必须通过返回值提交状态增量（配合 reducer 合并），原地修改在
LangGraph 分支副本中不会生效。任何 Agent 超时/结构错误/无效证据/总体
置信度不足 → NEEDS_REVIEW，不生成普通自动分流。
"""
import logging
import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, cast

from langgraph.graph import END, START, StateGraph

from app.contracts.matching import AgentResult, AnalysisSnapshot, RubricConfig
from app.domains.matching.agents import run_agent
from app.domains.matching.scoring import ROUTE_REVIEW, DeterministicScorer, ScoreResult
from app.infrastructure.model_gateway import ModelGateway, ModelNotConfiguredError, ProviderError

logger = logging.getLogger(__name__)

DIMENSIONS = ("basic", "skills", "experience")


def _keep_last_reason(current: str | None, new: str | None) -> str | None:
    return new if new is not None else current


@dataclass
class MatchingState:
    snapshot: AnalysisSnapshot
    rubric: RubricConfig
    valid_block_ids: set[str]
    gateway: ModelGateway | None = None
    agent_results: Annotated[dict[str, AgentResult], operator.or_] = field(default_factory=dict)
    agent_errors: Annotated[list[str], operator.add] = field(default_factory=list)
    needs_review: Annotated[bool, operator.or_] = False
    review_reason: Annotated[str | None, _keep_last_reason] = None
    score: ScoreResult | None = None


def _run_one_agent(state: MatchingState, dimension: str) -> dict[str, Any]:
    if state.needs_review:
        return {}
    if state.gateway is None:
        return {"needs_review": True, "review_reason": "MODEL_NOT_CONFIGURED"}
    try:
        result = run_agent(state.gateway, state.snapshot, dimension, state.valid_block_ids)
        return {"agent_results": {dimension: result}}
    except ModelNotConfiguredError:
        return {"needs_review": True, "review_reason": "MODEL_NOT_CONFIGURED"}
    except ProviderError as exc:
        return {"needs_review": True, "review_reason": exc.code, "agent_errors": [f"{dimension}: {exc}"]}
    except Exception as exc:
        return {"needs_review": True, "review_reason": "AGENT_FAILED", "agent_errors": [f"{dimension}: {exc}"]}


def _supervisor_validate(state: MatchingState) -> dict[str, Any]:
    """Supervisor 只验证冲突、缺失与证据覆盖，不修改权重或阈值。"""
    if state.needs_review:
        return {}
    if len(state.agent_results) != len(DIMENSIONS):
        return {"needs_review": True, "review_reason": "AGENT_RESULTS_INCOMPLETE"}
    for dimension in DIMENSIONS:
        result = state.agent_results.get(dimension)
        if result is None or not result.evidence_refs:
            return {"needs_review": True, "review_reason": f"MISSING_EVIDENCE:{dimension}"}
    return {}


def _deterministic_score(state: MatchingState) -> dict[str, Any]:
    if state.needs_review:
        return {}
    score = DeterministicScorer(state.rubric).score(state.agent_results)
    if score.route == ROUTE_REVIEW:
        return {"score": score, "needs_review": True, "review_reason": "LOW_CONFIDENCE"}
    return {"score": score}


def build_matching_graph() -> Any:
    graph = StateGraph(MatchingState)
    graph.add_node("basic_agent", lambda state: _run_one_agent(state, "basic"))
    graph.add_node("skill_agent", lambda state: _run_one_agent(state, "skills"))
    graph.add_node("experience_agent", lambda state: _run_one_agent(state, "experience"))
    graph.add_node("supervisor_validate", _supervisor_validate)
    graph.add_node("deterministic_score", _deterministic_score)
    graph.add_edge(START, "basic_agent")
    graph.add_edge(START, "skill_agent")
    graph.add_edge(START, "experience_agent")
    graph.add_edge("basic_agent", "supervisor_validate")
    graph.add_edge("skill_agent", "supervisor_validate")
    graph.add_edge("experience_agent", "supervisor_validate")
    graph.add_edge("supervisor_validate", "deterministic_score")
    graph.add_edge("deterministic_score", END)
    return graph.compile()


def run_matching(
    *,
    snapshot: AnalysisSnapshot,
    rubric: RubricConfig,
    valid_block_ids: set[str],
    gateway: ModelGateway | None = None,
) -> MatchingState:
    state = MatchingState(
        snapshot=snapshot,
        rubric=rubric,
        valid_block_ids=valid_block_ids,
        gateway=gateway,
    )
    try:
        result = build_matching_graph().invoke(state)
        if isinstance(result, dict):
            return MatchingState(**result)
        return cast(MatchingState, result)
    except Exception as exc:  # LangGraph 内部异常兜底：显式 NEEDS_REVIEW
        logger.exception("Matching 图执行异常")
        state.needs_review = True
        state.review_reason = "GRAPH_FAILED"
        state.agent_errors.append(str(exc))
        return state
