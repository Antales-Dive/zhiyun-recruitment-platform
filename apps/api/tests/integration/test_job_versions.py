"""岗位版本集成测试：不可变版本、活动版本切换与规则版本。"""
import json

from app.contracts.matching import RubricConfig
from app.domains.recruitment.service import add_job_version, create_job
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.models import Job, JobVersion, RubricVersion


def setup_module():
    Base.metadata.create_all(bind=engine)


def db_session():
    session = SessionLocal()
    yield session
    session.close()


class TestJobVersions:
    def test_create_job_generates_immutable_versions(self):
        db = next(db_session())
        rubric = RubricConfig(
            dimensions={"basic": 0.2, "skills": 0.4, "experience": 0.4},
            thresholds={"interview": 80, "questionnaire": 60, "talent_pool": 40},
        )
        created = create_job(
            db,
            org_id="org-a",
            title="AI 工程师",
            description="负责大模型应用",
            requirements=["Python", "RAG"],
            rubric=rubric,
        )

        assert created.job.active_version_id == created.job_version.id
        assert created.job_version.version_no == 1
        assert json.loads(created.job_version.requirements_json) == ["Python", "RAG"]
        assert created.rubric_version.version_no == 1
        assert json.loads(created.rubric_version.dimensions_json)["skills"] == 0.4
        assert created.rubric_version.confidence_threshold == rubric.confidence_threshold
        db.close()

    def test_add_version_keeps_old_version_intact(self):
        db = next(db_session())
        created = create_job(
            db, org_id="org-a", title="后端工程师", description="v1", requirements=["Python"]
        )
        old_version_id = created.job_version.id

        new_version = add_job_version(
            db, org_id="org-a", job_id=created.job.id, description="v2", requirements=["Python", "Go"]
        )

        assert new_version.version_no == 2
        assert new_version.id != old_version_id
        db.refresh(created.job)
        assert created.job.active_version_id == new_version.id

        # 旧版本数据不可变
        old = db.get(JobVersion, old_version_id)
        assert old.version_no == 1
        assert json.loads(old.requirements_json) == ["Python"]
        db.close()

    def test_new_version_requires_new_rubric_or_keeps_active(self):
        db = next(db_session())
        created = create_job(db, org_id="org-a", title="数据工程师", description="v1", requirements=["SQL"])
        new_version = add_job_version(
            db, org_id="org-a", job_id=created.job.id, description="v2", requirements=["SQL", "ETL"]
        )

        from sqlalchemy import select

        rubric = db.scalar(
            select(RubricVersion).where(RubricVersion.job_version_id == new_version.id)
        )
        assert rubric is not None
        db.close()

    def test_cross_org_job_version_rejected(self):
        db = next(db_session())
        created = create_job(db, org_id="org-a", title="岗位", description="d", requirements=[])

        from app.domains.recruitment.service import JobNotFoundError

        try:
            add_job_version(db, org_id="org-b", job_id=created.job.id, description="x", requirements=[])
            raised = False
        except JobNotFoundError:
            raised = True
        assert raised
        db.close()

    def test_jobs_table_has_active_version(self):
        db = next(db_session())
        created = create_job(db, org_id="org-a", title="活动版本岗位", description="d", requirements=[])
        job = db.get(Job, created.job.id)
        assert job.active_version_id == created.job_version.id
        db.close()
