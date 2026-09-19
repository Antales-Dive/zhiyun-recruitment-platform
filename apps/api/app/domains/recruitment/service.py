"""招聘域：岗位版本与评分规则版本（不可变快照，FR-001/FR-002）。"""
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.matching import RubricConfig
from app.infrastructure.models import Job, JobVersion, RubricVersion


class JobNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class CreatedJob:
    job: Job
    job_version: JobVersion
    rubric_version: RubricVersion


def create_job(
    db: Session,
    *,
    org_id: str,
    title: str,
    description: str,
    requirements: list[str],
    rubric: RubricConfig | None = None,
) -> CreatedJob:
    """创建岗位并生成不可变 job_version 与 rubric_version。"""
    job = Job(org_id=org_id, title=title.strip(), description=description, status="OPEN")
    db.add(job)
    db.flush()

    version = JobVersion(
        job_id=job.id,
        version_no=1,
        description=description,
        requirements_json=json.dumps(requirements, ensure_ascii=False),
    )
    db.add(version)
    db.flush()
    job.active_version_id = version.id

    rubric = rubric or RubricConfig()
    rubric_row = RubricVersion(
        job_version_id=version.id,
        version_no=1,
        dimensions_json=json.dumps(rubric.dimensions, ensure_ascii=False),
        thresholds_json=json.dumps(rubric.thresholds, ensure_ascii=False),
        confidence_threshold=rubric.confidence_threshold,
    )
    db.add(rubric_row)
    db.commit()
    return CreatedJob(job=job, job_version=version, rubric_version=rubric_row)


def add_job_version(
    db: Session,
    *,
    org_id: str,
    job_id: str,
    description: str,
    requirements: list[str],
) -> JobVersion:
    """新增岗位版本：旧版本保持不可变，新版本成为活动版本。

    新版本继承上一活动版本的评分规则快照（FR-002：每个岗位版本
    关联一个 rubric_version），后续可按需独立调整。
    """
    job = db.scalar(select(Job).where(Job.id == job_id, Job.org_id == org_id))
    if job is None:
        raise JobNotFoundError("岗位不存在或不在当前组织")
    max_no = db.scalar(
        select(JobVersion.version_no).where(JobVersion.job_id == job.id).order_by(JobVersion.version_no.desc())
    )
    version = JobVersion(
        job_id=job.id,
        version_no=(max_no or 0) + 1,
        description=description,
        requirements_json=json.dumps(requirements, ensure_ascii=False),
    )
    db.add(version)
    db.flush()

    previous_rubric = get_active_rubric(db, job)
    default_rubric = RubricConfig()
    if previous_rubric:
        dimensions_json = previous_rubric.dimensions_json
        thresholds_json = previous_rubric.thresholds_json
        confidence_threshold = previous_rubric.confidence_threshold
    else:
        dimensions_json = json.dumps(default_rubric.dimensions, ensure_ascii=False)
        thresholds_json = json.dumps(default_rubric.thresholds, ensure_ascii=False)
        confidence_threshold = default_rubric.confidence_threshold
    rubric_row = RubricVersion(
        job_version_id=version.id,
        version_no=1,
        dimensions_json=dimensions_json,
        thresholds_json=thresholds_json,
        confidence_threshold=confidence_threshold,
    )
    db.add(rubric_row)

    job.active_version_id = version.id
    db.commit()
    return version


def get_active_rubric(db: Session, job: Job) -> RubricVersion | None:
    if job.active_version_id is None:
        return None
    return db.scalar(
        select(RubricVersion).where(RubricVersion.job_version_id == job.active_version_id)
    )


def job_snapshot(db: Session, *, job_version_id: str, org_id: str) -> dict | None:
    """加载不可变岗位快照：岗位、版本与规则（Matching/Interview 共用）。"""
    row = db.execute(
        select(Job, JobVersion, RubricVersion)
        .select_from(JobVersion)
        .join(RubricVersion, RubricVersion.job_version_id == JobVersion.id)
        .join(Job, Job.id == JobVersion.job_id)
        .where(JobVersion.id == job_version_id, Job.org_id == org_id)
    ).first()
    if row is None:
        return None
    job, version, rubric = row
    return {
        "job": job,
        "job_version": version,
        "rubric_version": rubric,
        "rubric": RubricConfig(
            dimensions=json.loads(rubric.dimensions_json),
            thresholds=json.loads(rubric.thresholds_json),
            confidence_threshold=rubric.confidence_threshold,
        ),
        "requirements": json.loads(version.requirements_json or "[]"),
    }
