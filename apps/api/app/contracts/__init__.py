"""API 契约：与 legacy 兼容的响应/请求 Schema（TASK-001 基线）。"""
from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    skills: list[str] = Field(default_factory=list, max_length=30)


class JobResponse(BaseModel):
    id: str
    title: str
    description: str
    skills: list[str]
    version: int
    status: str


class TaskResponse(BaseModel):
    task_id: str
    candidate_id: str
    status: str
    progress: int


class CandidateResponse(BaseModel):
    id: str
    job_id: str
    name: str
    status: str
    task_id: str | None = None
    match_score: float | None = None
    route: str | None = None


class KnowledgeUploadResponse(BaseModel):
    document_id: str
    version_id: str
    task_id: str
    status: str


class KnowledgePublishResponse(BaseModel):
    document_id: str
    version_id: str
    status: str


class KnowledgeTaskResponse(BaseModel):
    task_id: str
    document_id: str
    version_id: str
    status: str
    progress: int
    last_error: str | None = None


class AssistantQueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


class CitationResponse(BaseModel):
    citation_id: str
    document_id: str
    document_title: str
    version: int
    section: str | None = None
    page_number: int | None = None
    excerpt: str
    score: float


class AssistantQueryResponse(BaseModel):
    query_id: str
    answer: str
    reliable: bool
    citations: list[CitationResponse]
    error_code: str | None = None
