"""RAG 查询链集成测试：发布可见性、授权过滤、引用与拒答。"""
import asyncio
import hashlib
import json

import httpx
import pytest
from app.domains.knowledge.version_service import VersionService
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.messaging.envelope import build_envelope, parse_envelope
from app.infrastructure.models import KnowledgeDocument
from app.workers.knowledge_worker import handle_knowledge_ingestion_requested


@pytest.fixture(scope="module", autouse=True)
def schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


def publish_document(db, *, org_id="org-a", title, content, roles=("HR", "INTERVIEWER", "AUDITOR")):
    from app.domains.tasks.outbox import OutboxService
    from app.domains.tasks.service import TaskService
    from app.infrastructure.files.store import FileStore

    document = KnowledgeDocument(org_id=org_id, title=title, status="DRAFT")
    db.add(document)
    db.flush()
    version = VersionService(db).create_version(
        document=document,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        original_name="doc.txt",
        stored_path="",
        content_type="text/plain",
        allowed_roles=list(roles),
    )
    store = FileStore()
    key = store.store(category="knowledge", suffix=".txt", content=content.encode("utf-8"))
    version.stored_path = key
    db.commit()

    task = TaskService(db).create(
        org_id=org_id, task_type="KNOWLEDGE_INGEST", aggregate_type="knowledge_version", aggregate_id=version.id
    )
    OutboxService(db).enqueue(
        org_id=org_id,
        event_type="knowledge.ingestion.requested",
        aggregate_type="knowledge_version",
        aggregate_id=version.id,
        payload={"task_id": task.id, "version_id": version.id},
    )
    db.commit()
    envelope = parse_envelope(
        json.dumps(
            build_envelope(
                event_id="evt-k",
                event_type="knowledge.ingestion.requested",
                aggregate_type="knowledge_version",
                aggregate_id=version.id,
                org_id=org_id,
                trace_id="test",
                payload={"task_id": task.id, "version_id": version.id},
            )
        )
    )
    handle_knowledge_ingestion_requested(db, envelope)
    db.expire_all()
    VersionService(db).publish(document=document, version_id=version.id)
    db.commit()
    return document, version


def query_api(db, question, role="HR", org_id="org-a"):
    async def run():
        from app.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/assistant/query",
                json={"question": question},
                headers={"X-User-Role": role, "X-Org-Id": org_id},
            )

    return asyncio.run(run())


class TestRagPipeline:
    def test_published_document_returns_evidence_and_citations(self, db):
        marker = "考勤异常处理流程"
        publish_document(db, title="考勤管理制度", content=f"第一章 考勤\n{marker}：迟到 30 分钟以上需提交说明。")

        response = query_api(db, f"{marker}是什么？")

        assert response.status_code == 200
        body = response.json()["data"]
        # Provider 未配置：显式状态 + 授权证据
        assert body["error_code"] == "MODEL_NOT_CONFIGURED"
        assert len(body["citations"]) >= 1
        assert body["citations"][0]["document_title"] == "考勤管理制度"

    def test_unpublished_document_is_not_searchable(self, db):
        marker = "unpubdoc-88q7zk9"
        document = KnowledgeDocument(org_id="org-a", title="草稿制度", status="DRAFT")
        db.add(document)
        db.commit()

        response = query_api(db, marker)

        body = response.json()["data"]
        assert body["reliable"] is False
        assert body["error_code"] == "NO_RELIABLE_EVIDENCE"
        assert body["citations"] == []

    def test_no_evidence_returns_explicit_refusal(self, db):
        response = query_api(db, "nosuch-policy-404-xyz")

        body = response.json()["data"]
        assert body["reliable"] is False
        assert body["error_code"] == "NO_RELIABLE_EVIDENCE"

    def test_retrieval_run_is_persisted(self, db):
        marker = "费用报销流程"
        publish_document(db, title="报销制度", content=f"第一章\n{marker}：需附发票原件。")

        query_api(db, f"{marker}需要什么？")

        from app.infrastructure.models import RetrievalRun

        run = db.query(RetrievalRun).order_by(RetrievalRun.created_at.desc()).first()
        assert run is not None
        assert run.config_version == "rag-chain-v1"
        assert json.loads(run.filter_json)["role"] == "HR"


class TestRagAuthorization:
    def test_disallowed_role_gets_no_evidence(self, db):
        marker = "薪酬保密条款"
        publish_document(db, title="薪酬制度", content=f"第一章\n{marker}：仅限 HR 查看。", roles=("HR",))

        hr_response = query_api(db, marker, role="HR")
        auditor_response = query_api(db, marker, role="AUDITOR")

        assert len(hr_response.json()["data"]["citations"]) >= 1
        assert auditor_response.json()["data"]["error_code"] == "NO_RELIABLE_EVIDENCE"
        assert auditor_response.json()["data"]["citations"] == []

    def test_cross_org_document_is_invisible(self, db):
        marker = "乙组织专有制度"
        publish_document(db, org_id="org-b", title="乙组织制度", content=f"第一章\n{marker}：内容仅乙组织可见。")

        response = query_api(db, marker, org_id="org-a")

        assert response.json()["data"]["error_code"] == "NO_RELIABLE_EVIDENCE"
