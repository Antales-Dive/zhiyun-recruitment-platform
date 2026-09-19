"""匹配域契约：Agent 输出 Schema、Rubric 配置与分析快照。

本模块是 Agent 链路的"类型边界"，被三方共享：
  agents.py 用它校验模型输出；graph.py 用它做状态字段类型；
  workers/matching_consumer.py 用它落库；tests/ 用它构造桩数据。
放在 contracts/ 而非 domains/ 的原因：契约必须比实现稳定——
改这里等于改 API/存储语义，需要同步迁移与评估集，不能随手在业务代码里改。
契约层不含任何数据库查询与网络调用。
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# 封闭集合而非 str：模型若返回 "skill"（少个 s）或中文维度名，Literal 校验直接失败，
# 不会因为"看起来像个字符串"就静默进入算分。新增维度需同时改 graph 的 DIMENSIONS
# 与 agents 的 _DIMENSION_DEFINITIONS。
AgentDimension = Literal["basic", "skills", "experience"]

# 默认规则的单一事实来源：job_routes 与 recruitment 服务在建岗/建版本时都回落到
# RubricConfig() 的这套默认值，测试用例同样以它为基线。
# 分流阈值 80/60/40 与 confidence 门 0.6 属业务政策，调整要走规则版本而非改码。
ROUTE_THRESHOLDS = {"interview": 80, "questionnaire": 60, "talent_pool": 40}
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_DIMENSIONS = {"basic": 0.2, "skills": 0.4, "experience": 0.4}


class AgentResult(BaseModel):
    """单个 Agent 的结构化输出；证据必须引用简历块 ID（AC-004）。

    这个模型就是"提示词里要求模型输出的那个 JSON"，两者必须同步修改：
    字段名写错 → model_validate 失败 → INVALID_RESPONSE → 整单转人工（不会给错分）。
    """

    dimension: AgentDimension
    # ge/le 把"模型给 120 分""给 -5 分"这类越界输出在入口就拒掉，
    # 而不是让脏数据流进加权求和后产生 >100 的总分。
    raw_score: int = Field(ge=0, le=100)
    # confidence 是模型自评，不可直接信任；只在算分末端用作质量门（scoring.py）。
    confidence: float = Field(ge=0.0, le=1.0)
    # 证据引用：graph 的 Supervisor 要求非空；具体 ID 是否真实存在由 agents.parse 校验。
    evidence_refs: list[str] = Field(default_factory=list)
    # 缺项与理由码是给 HR 看的解释文本，不参与算分，避免"解释"影响"结论"。
    missing_fields: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class RubricConfig(BaseModel):
    """版本化评分规则：维度权重、分流阈值与置信度阈值。

    落库形态是 RubricVersion 表的 dimensions_json / thresholds_json /
    confidence_threshold 三列（recruitment 服务写入，job_snapshot 读出后重组为本模型）。
    岗位版本创建时即固化一份规则，因此历史分析结论永远按其当时的规则复现。
    两个 field_validator 让"坏规则"在建岗/改规则这一步就报错，而不是等算分时静默出错。
    """

    dimensions: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_DIMENSIONS))
    thresholds: dict[str, int] = Field(default_factory=lambda: dict(ROUTE_THRESHOLDS))
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD

    @field_validator("dimensions")
    @classmethod
    def weights_must_sum_to_one(cls, value: dict[str, float]) -> dict[str, float]:
        """权重和为 1 是"总分仍落在 0-100"的唯一保证。

        若允许权重和为 2，raw_score=100 的候选人总分会是 200，80/60/40 阈值立即失去意义。
        用 1e-6 容差而非 ==，因为浮点累加不精确（0.2+0.4+0.4）。
        """
        if not value:
            raise ValueError("维度权重不能为空")
        if abs(sum(value.values()) - 1.0) > 1e-6:
            raise ValueError("维度权重之和必须为 1")
        return value

    @field_validator("thresholds")
    @classmethod
    def thresholds_must_be_ordered(cls, value: dict[str, int]) -> dict[str, int]:
        """阈值必须严格递减，否则会出现"进人才库的分数线高于进面试"这种倒挂规则。

        在入口拒绝，使得 scoring.py 可以放心按降序 elif 比较而无需再校验顺序。
        """
        if not (value["interview"] > value["questionnaire"] > value["talent_pool"]):
            raise ValueError("分流阈值必须满足 interview > questionnaire > talent_pool")
        return value


class AnalysisSnapshot(BaseModel):
    """Agent 输入：不可变岗位版本、规则版本与简历版本最小必要文本。

    "最小必要"指只传评估必需的三类内容（岗位标题/描述/要求 + 简历原文块 +
    三个版本 ID），不携带账号、组织配置、其他候选人等数据库上下文。
    注意：简历原文块本身可能含手机号、邮箱等候选人联系方式——这是匹配的必需输入，
    无法在此剔除；因此 ModelRun 只记 provider/model/prompt_version/token 等元数据，
    不落 Prompt 正文，避免调用留痕变成二次泄露面。
    三个 *_version_id 是复现凭证：凭它们可以重新拉出完全相同的输入。
    """

    job_version_id: str
    rubric_version_id: str
    resume_version_id: str
    job_title: str
    job_description: str
    requirements: list[str]
    # 保持 list[dict] 而非再建一个 Pydantic 模型：块由 ResumeBlock ORM 直接映射而来
    # （{id, section, text}），此处只做输入容器，不需要二次校验；
    # ID 的合法性校验走 valid_block_ids 集合（graph 传给 agents 做幻觉引用检查）。
    resume_blocks: list[dict]  # [{id, section, text}]

