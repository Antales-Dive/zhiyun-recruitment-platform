"""匹配域契约：Agent 输出 Schema、Rubric 配置与分析快照。"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

AgentDimension = Literal["basic", "skills", "experience"]

ROUTE_THRESHOLDS = {"interview": 80, "questionnaire": 60, "talent_pool": 40}
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_DIMENSIONS = {"basic": 0.2, "skills": 0.4, "experience": 0.4}


class AgentResult(BaseModel):
    """单个 Agent 的结构化输出；证据必须引用简历块 ID（AC-004）。"""

    dimension: AgentDimension
    raw_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class RubricConfig(BaseModel):
    """版本化评分规则：维度权重、分流阈值与置信度阈值。"""

    dimensions: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_DIMENSIONS))
    thresholds: dict[str, int] = Field(default_factory=lambda: dict(ROUTE_THRESHOLDS))
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD

    @field_validator("dimensions")
    @classmethod
    def weights_must_sum_to_one(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("维度权重不能为空")
        if abs(sum(value.values()) - 1.0) > 1e-6:
            raise ValueError("维度权重之和必须为 1")
        return value

    @field_validator("thresholds")
    @classmethod
    def thresholds_must_be_ordered(cls, value: dict[str, int]) -> dict[str, int]:
        if not (value["interview"] > value["questionnaire"] > value["talent_pool"]):
            raise ValueError("分流阈值必须满足 interview > questionnaire > talent_pool")
        return value


class AnalysisSnapshot(BaseModel):
    """Agent 输入：不可变岗位版本、规则版本与简历版本最小必要文本。"""

    job_version_id: str
    rubric_version_id: str
    resume_version_id: str
    job_title: str
    job_description: str
    requirements: list[str]
    resume_blocks: list[dict]  # [{id, section, text}]
