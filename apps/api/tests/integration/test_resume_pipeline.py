"""简历流水线集成测试：导入 → 解析 → 质量门 → 去重/幂等/跨组织。"""
import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from app.domains.documents.service import import_resume_files
from app.domains.documents.validation import FileValidationError
from app.domains.tasks.idempotency import IdempotencyConflict
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine

VALID_RESUME = "姓名：张三\n邮箱 zhangsan@example.com\n电话 13800138000\n技能：Python、FastAPI\n5 年工作经验".encode()


@pytest.fixture(scope="module", autouse=True)
def schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


def create_job(db, org_id="org-a"):
    from app.infrastructure.models import Job

    job = Job(org_id=org_id, title="Python 工程师", description="后端", skills_json='["Python"]')
    db.add(job)
    db.commit()
    return job


def run_handler(task_id, candidate_id, version_id, file_id):
    from app.infrastructure.messaging.envelope import build_envelope, parse_envelope
    from app.workers.resume_worker import handle_resume_parse_requested

    envelope = parse_envelope(
        json.dumps(
            build_envelope(
                event_id="evt-x",
                event_type="resume.parse.requested",
                aggregate_type="candidate",
                aggregate_id=candidate_id,
                org_id="org-a",
                trace_id="test",
                payload={"task_id": task_id, "file_id": file_id, "resume_version_id": version_id},
            )
        )
    )
    db = SessionLocal()
    try:
        handle_resume_parse_requested(db, envelope)
    finally:
        db.close()


def latest_task(db, candidate_id):
    from app.infrastructure.models import Task
    from sqlalchemy import select

    return db.scalar(
        select(Task).where(Task.candidate_id == candidate_id).order_by(Task.created_at.desc())
    )


class TestImportService:
    def test_import_creates_candidate_version_and_outbox_event(self, db):
        job = create_job(db)

        results = import_resume_files(
            db,
            org_id="org-a",
            actor_id="u1",
            job_id=job.id,
            files=[("zhangsan.txt", VALID_RESUME, "text/plain")],
            idempotency_key=None,
        )

        from app.infrastructure.models import OutboxEvent, ResumeVersion

        assert len(results) == 1
        assert results[0].reused is False
        version = db.query(ResumeVersion).filter_by(candidate_id=results[0].candidate_id).one()
        assert version.status == "PENDING"
        event = db.query(OutboxEvent).filter_by(aggregate_id=results[0].candidate_id).one()
        assert event.event_type == "resume.parse.requested"
        assert event.payload_json != ""

    def test_same_org_duplicate_file_is_reused(self, db):
        job = create_job(db)
        first = import_resume_files(
            db, org_id="org-a", actor_id="u1", job_id=job.id,
            files=[("a.txt", VALID_RESUME, "text/plain")], idempotency_key=None,
        )
        second = import_resume_files(
            db, org_id="org-a", actor_id="u1", job_id=job.id,
            files=[("a-copy.txt", VALID_RESUME, "text/plain")], idempotency_key=None,
        )

        assert second[0].reused is True
        assert second[0].candidate_id == first[0].candidate_id
        assert second[0].task_id == first[0].task_id

    def test_cross_org_duplicate_creates_independent_candidate(self, db):
        job_a = create_job(db, "org-a")
        job_b = create_job(db, "org-b")

        first = import_resume_files(
            db, org_id="org-a", actor_id="u1", job_id=job_a.id,
            files=[("same.txt", VALID_RESUME, "text/plain")], idempotency_key=None,
        )
        second = import_resume_files(
            db, org_id="org-b", actor_id="u2", job_id=job_b.id,
            files=[("same.txt", VALID_RESUME, "text/plain")], idempotency_key=None,
        )

        assert second[0].reused is False
        assert second[0].candidate_id != first[0].candidate_id

    def test_idempotency_key_reuse_returns_same_result(self, db):
        job = create_job(db)
        first = import_resume_files(
            db, org_id="org-a", actor_id="u1", job_id=job.id,
            files=[("idem.txt", VALID_RESUME, "text/plain")], idempotency_key="key-1",
        )
        second = import_resume_files(
            db, org_id="org-a", actor_id="u1", job_id=job.id,
            files=[("idem.txt", VALID_RESUME, "text/plain")], idempotency_key="key-1",
        )

        assert [item.candidate_id for item in first] == [item.candidate_id for item in second]

    def test_idempotency_key_with_different_payload_conflicts(self, db):
        job = create_job(db)
        import_resume_files(
            db, org_id="org-a", actor_id="u1", job_id=job.id,
            files=[("idem.txt", VALID_RESUME, "text/plain")], idempotency_key="key-2",
        )

        with pytest.raises(IdempotencyConflict):
            import_resume_files(
                db, org_id="org-a", actor_id="u1", job_id=job.id,
                files=[("other.txt", b"different content", "text/plain")], idempotency_key="key-2",
            )

    def test_invalid_file_signature_rejected(self, db):
        job = create_job(db)

        with pytest.raises(FileValidationError) as exc:
            import_resume_files(
                db, org_id="org-a", actor_id="u1", job_id=job.id,
                files=[("fake.pdf", b"not a pdf at all", "application/pdf")], idempotency_key=None,
            )
        assert exc.value.code == "INVALID_FILE_SIGNATURE"

    def test_cross_org_job_rejected(self, db):
        create_job(db, "org-b")

        with pytest.raises(FileValidationError) as exc:
            import_resume_files(
                db, org_id="org-a", actor_id="u1", job_id=create_job(db, "org-b").id,
                files=[("a.txt", VALID_RESUME, "text/plain")], idempotency_key=None,
            )
        assert exc.value.code == "JOB_NOT_FOUND"


class TestParsePipeline:
    def test_full_pipeline_parses_and_persists_blocks(self, db):
        job = create_job(db)
        result = import_resume_files(
            db, org_id="org-a", actor_id="u1", job_id=job.id,
            files=[("zhangsan.txt", VALID_RESUME, "text/plain")], idempotency_key=None,
        )[0]

        from app.infrastructure.models import ResumeVersion

        version = db.query(ResumeVersion).filter_by(candidate_id=result.candidate_id).one()
        run_handler(result.task_id, result.candidate_id, version.id, version.file_id)

        from app.infrastructure.models import Candidate, ParsedProfile, ResumeBlock, Task
        from sqlalchemy import select

        db.expire_all()
        candidate = db.get(Candidate, result.candidate_id)
        task = db.get(Task, result.task_id)
        profile = db.scalar(select(ParsedProfile).where(ParsedProfile.resume_version_id == version.id))
        blocks = list(db.scalars(select(ResumeBlock).where(ResumeBlock.resume_version_id == version.id)).all())

        assert task.status == "SUCCEEDED"
        assert candidate.status == "PARSED"
        assert profile is not None
        parsed_profile = json.loads(profile.profile_json)
        assert parsed_profile["name"] == "张三"
        assert parsed_profile["email"] == "zhangsan@example.com"
        assert len(blocks) > 0
        assert blocks[0].text_hash

    def test_low_quality_resume_enters_review(self, db):
        job = create_job(db)
        result = import_resume_files(
            db, org_id="org-a", actor_id="u1", job_id=job.id,
            files=[("tiny.txt", "张三".encode(), "text/plain")], idempotency_key=None,
        )[0]

        from app.infrastructure.models import ResumeVersion

        version = db.query(ResumeVersion).filter_by(candidate_id=result.candidate_id).one()
        run_handler(result.task_id, result.candidate_id, version.id, version.file_id)

        from app.infrastructure.models import Candidate, Task

        db.expire_all()
        assert db.get(Task, result.task_id).status == "NEEDS_REVIEW"
        assert db.get(Candidate, result.candidate_id).status == "NEEDS_REVIEW"


class TestImportApi:
    def test_api_import_returns_202_with_items(self, db):
        job = create_job(db)
        unique_resume = VALID_RESUME + f"\n标记 {uuid4().hex}".encode()

        async def run():
            from app.main import app

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/v1/candidates/import",
                    data={"job_id": job.id},
                    files={"files": ("api.txt", unique_resume, "text/plain")},
                    headers={"X-User-Role": "HR", "X-Org-Id": "org-a"},
                )

        response = asyncio.run(run())

        assert response.status_code == 202
        item = response.json()["data"]["imports"][0]
        assert item["reused"] is False
        assert item["candidate_id"]

    def test_api_rejects_unsupported_type(self, db):
        job = create_job(db)

        async def run():
            from app.main import app

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/v1/candidates/import",
                    data={"job_id": job.id},
                    files={"files": ("evil.exe", b"MZ...", "application/octet-stream")},
                    headers={"X-User-Role": "HR", "X-Org-Id": "org-a"},
                )

        response = asyncio.run(run())

        assert response.status_code == 415
        assert response.json()["error"]["code"] == "FILE_TYPE_UNSUPPORTED"
