"""知识域：不可变版本、ACL、索引清单与发布/撤回（FR-021~FR-024）。"""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.audit.service import AuditService
from app.infrastructure.models import (
    KnowledgeAcl,
    KnowledgeDocument,
    KnowledgeIndexManifest,
    KnowledgeVersion,
    now_utc,
)


class KnowledgeStateError(ValueError):
    pass


class VersionService:
    def __init__(self, db: Session):
        self.db = db

    def create_version(
        self,
        *,
        document: KnowledgeDocument,
        content_sha256: str,
        original_name: str,
        stored_path: str,
        content_type: str,
        allowed_roles: list[str],
        actor_id: str | None = None,
        trace_id: str | None = None,
    ) -> KnowledgeVersion:
        """新增不可变版本；旧版本数据永不覆盖（FR-021）。"""
        max_no = self.db.scalar(
            select(KnowledgeVersion.version_number)
            .where(KnowledgeVersion.document_id == document.id)
            .order_by(KnowledgeVersion.version_number.desc())
        )
        version = KnowledgeVersion(
            document_id=document.id,
            version_number=(max_no or 0) + 1,
            status="UPLOADED",
            original_name=original_name,
            stored_path=stored_path,
            content_type=content_type,
            content_sha256=content_sha256,
            allowed_roles_json=json.dumps(allowed_roles, ensure_ascii=False),
        )
        self.db.add(version)
        self.db.flush()
        for role in allowed_roles:
            self.db.add(KnowledgeAcl(version_id=version.id, subject_type="role", subject_value=role))
        AuditService(self.db).record(
            org_id=document.org_id,
            actor_id=actor_id,
            action="KNOWLEDGE_VERSION_CREATED",
            resource_type="knowledge_version",
            resource_id=version.id,
            after={"version_number": version.version_number, "roles": allowed_roles},
            trace_id=trace_id,
        )
        return version

    def publish(
        self,
        *,
        document: KnowledgeDocument,
        version_id: str,
        expected_row_version: int | None = None,
        actor_id: str | None = None,
        trace_id: str | None = None,
    ) -> KnowledgeVersion:
        """原子切换活动版本：旧 PUBLISHED → SUPERSEDED（AC-015）。"""
        if expected_row_version is not None and document.row_version != expected_row_version:
            raise KnowledgeStateError("VERSION_CONFLICT")
        version = self.db.get(KnowledgeVersion, version_id)
        if version is None or version.document_id != document.id:
            raise KnowledgeStateError("KNOWLEDGE_VERSION_NOT_FOUND")
        if version.status != "INDEXED":
            raise KnowledgeStateError("KNOWLEDGE_VERSION_NOT_READY")
        manifest = self.db.scalar(
            select(KnowledgeIndexManifest)
            .where(
                KnowledgeIndexManifest.version_id == version.id,
                KnowledgeIndexManifest.status == "COMMITTED",
            )
            .order_by(KnowledgeIndexManifest.index_version.desc())
        )
        if manifest is None:
            raise KnowledgeStateError("INDEX_NOT_COMMITTED")

        for previous in document.versions:
            if previous.status == "PUBLISHED":
                previous.status = "SUPERSEDED"
        version.status = "PUBLISHED"
        version.published_at = now_utc()
        document.active_version_id = version.id
        document.status = "PUBLISHED"
        document.row_version += 1
        AuditService(self.db).record(
            org_id=document.org_id,
            actor_id=actor_id,
            action="KNOWLEDGE_PUBLISHED",
            resource_type="knowledge_version",
            resource_id=version.id,
            after={"version_number": version.version_number},
            trace_id=trace_id,
        )
        return version

    def withdraw(
        self,
        *,
        document: KnowledgeDocument,
        version_id: str,
        reason: str | None,
        actor_id: str | None = None,
        trace_id: str | None = None,
    ) -> KnowledgeVersion:
        """撤回活动版本：立即不可检索（FR-023）。"""
        version = self.db.get(KnowledgeVersion, version_id)
        if version is None or version.document_id != document.id:
            raise KnowledgeStateError("KNOWLEDGE_VERSION_NOT_FOUND")
        if document.active_version_id != version.id:
            raise KnowledgeStateError("NOT_ACTIVE_VERSION")
        version.status = "WITHDRAWN"
        document.active_version_id = None
        document.status = "DRAFT"
        document.row_version += 1
        AuditService(self.db).record(
            org_id=document.org_id,
            actor_id=actor_id,
            action="KNOWLEDGE_WITHDRAWN",
            resource_type="knowledge_version",
            resource_id=version.id,
            reason=reason,
            trace_id=trace_id,
        )
        return version

    def acl_roles(self, version_id: str) -> list[str]:
        rows = self.db.scalars(
            select(KnowledgeAcl.subject_value).where(
                KnowledgeAcl.version_id == version_id,
                KnowledgeAcl.subject_type == "role",
            )
        ).all()
        return list(rows)


class IndexManifestService:
    def __init__(self, db: Session):
        self.db = db

    def begin(self, version_id: str) -> KnowledgeIndexManifest:
        max_version = self.db.scalar(
            select(KnowledgeIndexManifest.index_version)
            .where(KnowledgeIndexManifest.version_id == version_id)
            .order_by(KnowledgeIndexManifest.index_version.desc())
        )
        manifest = KnowledgeIndexManifest(
            version_id=version_id,
            index_version=(max_version or 0) + 1,
            dense_count=0,
            sparse_count=0,
            status="BUILDING",
        )
        self.db.add(manifest)
        self.db.flush()
        return manifest

    def commit(self, manifest: KnowledgeIndexManifest, *, dense_count: int, sparse_count: int) -> None:
        """Dense/Sparse 数量一致后才可提交（FR-022）。"""
        if dense_count != sparse_count:
            raise KnowledgeStateError("INDEX_COUNT_MISMATCH")
        manifest.dense_count = dense_count
        manifest.sparse_count = sparse_count
        manifest.status = "COMMITTED"
        manifest.committed_at = now_utc()
