"""Matching LangGraph：三路并行 Agent → Supervisor 校验 → 确定性评分。

流程（03-architecture.md §5.2）：
  load_snapshot -> fan_out(basic/skill/experience) -> supervisor_validate
  -> deterministic_score -> confidence_gate -> route_or_human_review -> persist

并行节点必须通过返回值提交状态增量（配合 reducer 合并），原地修改在
LangGraph 分支副本中不会生效。任何 Agent 超时/结构错误/无效证据/总体
置信度不足 → NEEDS_REVIEW，不生成普通自动分流。

【为什么用图，而不是一段顺序 for 循环】
1) 三路 Agent 互不依赖，图把它们的调用意图声明为"同一超步内的并行分支"，
   而不是靠代码书写顺序被迫串行；耗时由三路之和收敛为取决于最慢的一路；
2) 汇合点（supervisor_validate）天然表达"三路都到齐才能继续"的 join 语义；
3) 失败要能归因：哪一路、什么错误码，必须落到状态里供 Worker 持久化成
   AgentRun / ModelRun，图的状态机比散落的 try/except 更可审计；
4) 拓扑变更（加第四路、去掉 Supervisor）只改边，不改业务函数。

【为什么分数不在图里"由模型决定"】
图里唯一的算分节点是 deterministic_score，它只读 Rubric 版本化的权重与
阈值。Agent 只交证据和维度原始分，Supervisor 只做完整性/证据覆盖校验，
两者都没有修改规则的权限——这是"AI 提取证据、规则做决策"的落地方式。
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

# 三路维度键，顺序即持久化 AgentRun 时的遍历顺序（workers/matching_consumer.py 复用此常量）。
# 增删维度要同时改四处：这里、agents.py 的 _DIMENSION_DEFINITIONS、contracts 的 AgentDimension
# 字面量集合、contracts 的 DEFAULT_DIMENSIONS 权重。漏改任一处都会在
# supervisor_validate 的"三路齐全"检查处暴露为需复核，而不会给出偏高的分数。
DIMENSIONS = ("basic", "skills", "experience")


def _keep_last_reason(current: str | None, new: str | None) -> str | None:
    """review_reason 的 reducer：只有非 None 的新值才覆盖已有原因。

    三路并行都可能写同一个字段，没有 reducer 的字段在并发写下会冲突。
    语义取"保留第一个真实原因、忽略后续 None 覆写"：None 表示"本分支没有新结论"，
    不是"清空原因"。注意多个分支同时给出不同错误码时，最终保留哪一个不保证确定顺序，
    因此全部细节都要同时记进 agent_errors 列表（用 operator.add 无损累加）。
    """
    return new if new is not None else current


@dataclass
class MatchingState:
    """图的状态模式（state schema）：一次候选人匹配所需的全部上下文与中间产物。

    只读输入（由 Worker 在 invoke 前注入，节点不改写）：
        snapshot        不可变分析快照：岗位文本 + 要求 + 简历原文块。
        rubric          版本化评分规则（权重/阈值/置信度门），确定性算分的唯一依据。
        valid_block_ids 简历块合法 ID 全集，用于拒绝幻觉证据引用。
        gateway         Model Gateway；None 表示 Provider 未配置，直接走人工复核。

    带 Annotated reducer 的字段是"多分支可合并写入"的输出；其余字段为单写者字段。
    """

    snapshot: AnalysisSnapshot
    rubric: RubricConfig
    valid_block_ids: set[str]
    gateway: ModelGateway | None = None
    # dict 合并：三路各写自己的维度键，operator.or_ 让 {basic:..} 与 {skills:..} 共存。
    agent_results: Annotated[dict[str, AgentResult], operator.or_] = field(default_factory=dict)
    # 列表追加：每路的错误说明都保留，供 Worker 拼成 error_summary 给人看。
    agent_errors: Annotated[list[str], operator.add] = field(default_factory=list)
    # 布尔或：任一路判定需复核 → 整体需复核（fail-closed，不可被后到的 False 翻回）。
    needs_review: Annotated[bool, operator.or_] = False
    review_reason: Annotated[str | None, _keep_last_reason] = None
    # 单写者字段：只有 deterministic_score 节点产出，无并发。
    score: ScoreResult | None = None


def _run_one_agent(state: MatchingState, dimension: str) -> dict[str, Any]:
    """一路 Agent 的节点体：成功提交结果，失败提交"需复核 + 错误码"。

    返回 dict 是"状态增量"而非新状态；返回 {} 表示本节点对状态无贡献。
    本函数永不抛异常到图外——所有失败都收敛为 needs_review，
    保证 Worker 总能拿到一个可持久化、可解释的状态。
    """
    # 短路：已有别的路失败时不再发起模型调用（省一次计费与延迟）。
    if state.needs_review:
        return {}
    if state.gateway is None:
        return {"needs_review": True, "review_reason": "MODEL_NOT_CONFIGURED"}
    try:
        result = run_agent(state.gateway, state.snapshot, dimension, state.valid_block_ids)
        return {"agent_results": {dimension: result}}
    # except 顺序即优先级：ModelNotConfiguredError 是 ProviderError 的子类，
    # 必须排在前面，否则会被父类吞掉、错误码从 MODEL_NOT_CONFIGURED 退化为通用码。
    except ModelNotConfiguredError:
        return {"needs_review": True, "review_reason": "MODEL_NOT_CONFIGURED"}
    # Provider 层的错误码（TIMEOUT / UNAVAILABLE / AUTH_FAILED / INVALID_RESPONSE）
    # 直接作为 review_reason 落库，运维无需读日志即可分类统计。
    except ProviderError as exc:
        return {"needs_review": True, "review_reason": exc.code, "agent_errors": [f"{dimension}: {exc}"]}
    # 兜底：Schema 之外的意外异常（网络库内部错误、代码缺陷等）也要归类，
    # 绝不让"未知异常"变成"静默给分"。
    except Exception as exc:
        return {"needs_review": True, "review_reason": "AGENT_FAILED", "agent_errors": [f"{dimension}: {exc}"]}


def _supervisor_validate(state: MatchingState) -> dict[str, Any]:
    """Supervisor 只验证冲突、缺失与证据覆盖，不修改权重或阈值。

    这是"监督者不裁判"的边界：它有权否决（转人工），无权改分。
    一旦 Supervisor 能调权重，规则版本化就失效了，评估集也不再可比。
    """
    # 已有路失败：三路里任意一路失败即整体需复核，无需重复判定。
    if state.needs_review:
        return {}
    # join 完整性：三路结果数量必须等于维度数（reducer 合并后少一路=有路静默失败）。
    if len(state.agent_results) != len(DIMENSIONS):
        return {"needs_review": True, "review_reason": "AGENT_RESULTS_INCOMPLETE"}
    for dimension in DIMENSIONS:
        result = state.agent_results.get(dimension)
        # 有分数但没证据 = 不可解释，HR 无法向候选人说明依据，必须复核。
        # （evidence_refs 非空只是形式检查；引用是否"真实存在"已在 agents.parse 处校验。）
        if result is None or not result.evidence_refs:
            return {"needs_review": True, "review_reason": f"MISSING_EVIDENCE:{dimension}"}
    return {}


def _deterministic_score(state: MatchingState) -> dict[str, Any]:
    """唯一算分节点：按版本化 Rubric 加权求和并按阈值分流。

    即使 Supervisor 通过，算分仍可能判需复核——三路平均分低于 confidence_threshold
    时路由被改写为 NEEDS_REVIEW（置信度门），此时 score 依旧落库，
    便于人工看到"模型算出来是多少、为什么不信"。
    """
    if state.needs_review:
        return {}
    score = DeterministicScorer(state.rubric).score(state.agent_results)
    if score.route == ROUTE_REVIEW:
        return {"score": score, "needs_review": True, "review_reason": "LOW_CONFIDENCE"}
    return {"score": score}


def build_matching_graph() -> Any:
    """编译拓扑：START ─┬→ basic_agent ──────┐
                      ├→ skill_agent ────────┼→ supervisor_validate → deterministic_score → END
                      └→ experience_agent ───┘

    三条 START 边 = fan-out（同一超步并行分支）；三条入 supervisor 的边 = fan-in，
    LangGraph 等所有入边分支完成后才执行 Supervisor，因此节点里读到的是合并后的完整状态。
    无 checkpointer、无持久化会话：整图是一次性无状态计算，重放由消息队列负责，
    所以每次调用现编译（编译开销远小于一次模型调用）。
    """
    graph = StateGraph(MatchingState)
    # 节点复用同一实现，仅维度参数不同：lambda 是拓扑与业务逻辑之间的唯一胶水。
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
    """同步执行整图，返回最终状态；Worker 据此写 AnalysisRun / AgentRun / RoutingDecision。

    契约：返回值一定可读——要么带 score（可自动分流），要么 needs_review=True（转人工）。
    不存在"抛异常给调用方且什么都没记"的路径，因此消费侧可以无条件持久化，
    不会因分析失败而丢任务状态。
    """
    state = MatchingState(
        snapshot=snapshot,
        rubric=rubric,
        valid_block_ids=valid_block_ids,
        gateway=gateway,
    )
    try:
        result = build_matching_graph().invoke(state)
        # LangGraph 以字典形式回传合并后的完整状态（含未被改写的输入字段），
        # 重建成 MatchingState 才能给调用方稳定的属性访问接口；
        # 少数版本直接回传状态对象，因此保留 isinstance 分支而非假定类型。
        if isinstance(result, dict):
            return MatchingState(**result)
        return cast(MatchingState, result)
    except Exception as exc:  # LangGraph 内部异常兜底：显式 NEEDS_REVIEW
        # 拓扑、reducer 或依赖库自身抛错时，退回到入参状态对象直接标注失败原因，
        # 保证"图坏了"也等价于"结论是需人工"，而不是让消息进入无限重投。
        logger.exception("Matching 图执行异常")
        state.needs_review = True
        state.review_reason = "GRAPH_FAILED"
        state.agent_errors.append(str(exc))
        return state

