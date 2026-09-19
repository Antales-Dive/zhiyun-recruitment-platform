"""知识生命周期集成测试：版本新增、原子发布、撤回与索引清单。"""
import hashlib
import json

import pytest
from app.domains.knowledge.version_service import (
    IndexManifestService,
    KnowledgeStateError,
    VersionService,
)
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.messaging.envelope import build_envelope, parse_envelope
from app.infrastructure.models import KnowledgeDocument, KnowledgeVersion
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


def upload_document(
    db,
    *,
    title="测试制度",
    content="第一章 总则\n试用期员工请假需直属负责人审批。",
    allowed_roles=("HR",),
):
    """走领域服务创建文档与版本 1，直接完成摄取。"""
    from app.domains.knowledge.version_service import VersionService

    document = KnowledgeDocument(org_id="org-a", title=title, status="DRAFT")
    db.add(document)
    db.flush()
    version = VersionService(db).create_version(
        document=document,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        original_name="policy.txt",
        stored_path="",
        content_type="text/plain",
        allowed_roles=list(allowed_roles),
    )
    # 写入文件到本地存储
    from app.infrastructure.files.store import FileStore

    store = FileStore()
    key = store.store(category="knowledge", suffix=".txt", content=content.encode("utf-8"))
    version.stored_path = key
    db.commit()
    return document, version


def run_ingestion(db, document, version):
    from app.domains.tasks.outbox import OutboxService
    from app.domains.tasks.service import TaskService

    task = TaskService(db).create(
        org_id="org-a", task_type="KNOWLEDGE_INGEST", aggregate_type="knowledge_version", aggregate_id=version.id
    )
    OutboxService(db).enqueue(
        org_id="org-a",
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
                org_id="org-a",
                trace_id="test",
                payload={"task_id": task.id, "version_id": version.id},
            )
        )
    )
    handle_knowledge_ingestion_requested(db, envelope)
    db.expire_all()


def count_searchable(db, marker):
    """模拟检索可见性：查询已发布活动版本的块。"""

    from app.domains.knowledge.retrieval_service import retrieve_evidence

    return retrieve_evidence(db, question=marker, org_id="org-a", role="HR")


class TestVersionLifecycle:
    def test_draft_is_not_searchable(self, db):
        document, version = upload_document(db, content="第一章\n保密草稿条款 marker-1 未发布。")

        assert count_searchable(db, "marker-1") == []

    def test_publish_switches_active_version_atomically(self, db):
        document, version = upload_document(db, content="第一章\n发布条款 marker-2 已生效。")
        run_ingestion(db, document, version)

        VersionService(db).publish(document=document, version_id=version.id)
        db.commit()

        db.expire_all()
        refreshed = db.get(KnowledgeDocument, document.id)
        assert refreshed.active_version_id == version.id
        assert version.status == "PUBLISHED"
        assert len(count_searchable(db, "marker-2")) > 0

    def test_publish_supersedes_previous_version(self, db):
        document, version1 = upload_document(db, content="第一章\n旧条款 marker-3 已被替换。")
        run_ingestion(db, document, version1)
        VersionService(db).publish(document=document, version_id=version1.id)
        db.commit()

        # 新增版本 2 并发布
        version2 = VersionService(db).create_version(
            document=document,
            content_sha256=hashlib.sha256(b"new").hexdigest(),
            original_name="policy2.txt",
            stored_path="",
            content_type="text/plain",
            allowed_roles=["HR"],
        )
        from app.infrastructure.files.store import FileStore

        store = FileStore()
        key = store.store(category="knowledge", suffix=".txt", content="第一章\n新条款 marker-4 生效中。".encode())
        version2.stored_path = key
        db.commit()
        run_ingestion(db, document, version2)
        VersionService(db).publish(document=document, version_id=version2.id)
        db.commit()

        db.expire_all()
        v1 = db.get(KnowledgeVersion, version1.id)
        assert v1.status == "SUPERSEDED"
        assert count_searchable(db, "marker-3") == []
        assert len(count_searchable(db, "marker-4")) > 0

    def test_withdraw_makes_version_unsearchable_immediately(self, db):
        document, version = upload_document(db, content="第一章\n可撤回条款 marker-5。")
        run_ingestion(db, document, version)
        VersionService(db).publish(document=document, version_id=version.id)
        db.commit()

        VersionService(db).withdraw(document=document, version_id=version.id, reason="内容有误")
        db.commit()

        db.expire_all()
        refreshed = db.get(KnowledgeDocument, document.id)
        assert refreshed.active_version_id is None
        assert count_searchable(db, "marker-5") == []

    def test_publish_requires_committed_index_manifest(self, db):
        document, version = upload_document(db, content="第一章\n未摄取条款 marker-6。")

        with pytest.raises(KnowledgeStateError) as exc:
            VersionService(db).publish(document=document, version_id=version.id)
        assert "KNOWLEDGE_VERSION_NOT_READY" in str(exc.value)


class TestIndexManifest:
    def test_manifest_commits_when_counts_match(self, db):
        document, version = upload_document(db, content="第一章\n清单条款 marker-7。")
        manifest_service = IndexManifestService(db)
        manifest = manifest_service.begin(version.id)

        manifest_service.commit(manifest, dense_count=4, sparse_count=4)
        db.commit()

        assert manifest.status == "COMMITTED"
        assert manifest.committed_at is not None

    def test_manifest_rejects_count_mismatch(self, db):
        document, version = upload_document(db, content="第一章\n清单条款 marker-8。")
        manifest_service = IndexManifestService(db)
        manifest = manifest_service.begin(version.id)

        with pytest.raises(KnowledgeStateError, match="INDEX_COUNT_MISMATCH"):
            manifest_service.commit(manifest, dense_count=4, sparse_count=3)

    def test_publish_rejects_uncommitted_manifest(self, db):
        document, version = upload_document(db, content="第一章\n未提交清单条款 marker-9。")
        run_ingestion(db, document, version)

        # 人为将最新清单改回 BUILDING
        from app.infrastructure.models import KnowledgeIndexManifest
        from sqlalchemy import select

        manifest = db.scalar(
            select(KnowledgeIndexManifest)
            .where(KnowledgeIndexManifest.version_id == version.id)
            .order_by(KnowledgeIndexManifest.index_version.desc())
        )
        manifest.status = "BUILDING"
        db.commit()

        with pytest.raises(KnowledgeStateError):
            VersionService(db).publish(document=document, version_id=version.id)


class TestVersionApi:
    def test_initial_upload_uses_task_outbox_pipeline(self, db):
        import asyncio

        import httpx

        async def run():
            from app.main import app

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/v1/knowledge/documents",
                    data={"title": "首次上传制度", "allowed_roles": "HR"},
                    files={"file": ("policy.txt", "制度内容 marker-initial".encode(), "text/plain")},
                    headers={"X-User-Role": "ADMIN", "X-Org-Id": "org-a"},
                )

        response = asyncio.run(run())
        assert response.status_code == 202
        task_id = response.json()["data"]["task_id"]

        from app.infrastructure.models import OutboxEvent, Task

        task = db.get(Task, task_id)
        assert task is not None
        assert task.task_type == "KNOWLEDGE_INGEST"
        event = db.query(OutboxEvent).filter_by(aggregate_id=task.aggregate_id).one()
        assert event.event_type == "knowledge.ingestion.requested"
        assert task.id in event.payload_json

    def test_upload_new_version_via_api(self, db):
        import asyncio

        import httpx

        document, version = upload_document(db, content="第一章\nAPI 版本条款 marker-10。")
        run_ingestion(db, document, version)

        async def run():
            from app.main import app

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    f"/api/v1/knowledge/documents/{document.id}/versions",
                    files={"file": ("v2.txt", "第二章\n新版条款 marker-11 上线。".encode(), "text/plain")},
                    headers={"X-User-Role": "ADMIN", "X-Org-Id": "org-a"},
                )

        response = asyncio.run(run())

        assert response.status_code == 202
        data = response.json()["data"]
        assert data["version_id"]
        assert data["task_id"]

        from app.infrastructure.models import KnowledgeVersion

        new_version = db.get(KnowledgeVersion, data["version_id"])
        assert new_version.version_number == 2

    def test_withdraw_via_api_requires_admin(self, db):
        import asyncio

        import httpx

        document, version = upload_document(db, content="第一章\n撤回 API 条款 marker-12。")
        run_ingestion(db, document, version)
        VersionService(db).publish(document=document, version_id=version.id)
        db.commit()

        async def run():
            from app.main import app

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    f"/api/v1/knowledge/versions/{version.id}/withdraw",
                    data={"reason": "测试撤回"},
                    headers={"X-User-Role": "HR", "X-Org-Id": "org-a"},
                )

        response = asyncio.run(run())

        assert response.status_code == 403
