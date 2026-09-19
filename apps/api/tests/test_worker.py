"""简历解析 Worker 测试：TXT 解析、质量门、OCR 与事件驱动流程。"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from app.domains.documents.pipeline import assess_quality, extract_profile
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker


class ProfileExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = "sqlite:///./data/test.db"
        os.environ["UPLOAD_DIR"] = cls.temp_dir.name
        from app.infrastructure import models  # noqa: F401
        from app.infrastructure.db import Base, engine

        Base.metadata.create_all(bind=engine)
        cls.session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        from app.infrastructure.db import engine

        engine.dispose()
        cls.temp_dir.cleanup()

    def test_extracts_email_phone_and_name(self):
        from app.contracts.resume import ParsedBlock

        blocks = [
            ParsedBlock(text="姓名：李四", section=None),
            ParsedBlock(text="邮箱 li.si@example.com 电话 13800138000", section=None),
        ]
        profile = extract_profile(blocks)

        self.assertEqual(profile.name, "李四")
        self.assertEqual(profile.email, "li.si@example.com")
        self.assertEqual(profile.phone, "13800138000")

    def test_does_not_fabricate_missing_fields(self):
        from app.contracts.resume import ParsedBlock

        profile = extract_profile([ParsedBlock(text="只有一段普通文字没有联系方式", section=None)])

        self.assertIsNone(profile.email)
        self.assertIsNone(profile.phone)
        self.assertIsNone(profile.name)

    def test_quality_penalizes_missing_fields(self):
        from app.contracts.resume import ParsedBlock, ParsedProfile

        empty = extract_profile([ParsedBlock(text="普通文本内容，无关键字段", section=None)])
        report = assess_quality(empty, [ParsedBlock(text="x")], "txt")

        self.assertLess(report.confidence, 0.6)
        self.assertIn("email", report.missing_fields)

        complete = ParsedProfile(name="张三", email="a@b.com", phone="13800138000")
        good = assess_quality(complete, [ParsedBlock(text="完整内容")], "txt")
        self.assertGreaterEqual(good.confidence, 0.6)


class ResumeParseWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = "sqlite:///./data/test.db"
        os.environ["UPLOAD_DIR"] = cls.temp_dir.name
        from app.infrastructure import models  # noqa: F401
        from app.infrastructure.db import Base, SessionLocal, engine

        Base.metadata.create_all(bind=engine)
        cls.session_factory = SessionLocal

    @classmethod
    def tearDownClass(cls):
        from app.infrastructure.db import engine

        engine.dispose()
        cls.temp_dir.cleanup()

    def _create_import_entities(self, *, filename="resume.txt", content: bytes):
        from app.domains.tasks.service import TaskService
        from app.infrastructure.files.store import FileStore
        from app.infrastructure.models import Candidate, CandidateFile, Job, ResumeVersion

        db = self.session_factory()
        job = Job(org_id="default", title="Python 工程师", description="后端开发", skills_json='["Python"]')
        db.add(job)
        db.flush()
        candidate = Candidate(org_id="default", job_id=job.id, name="待解析候选人")
        db.add(candidate)
        db.flush()
        store = FileStore()
        key = store.store(category="resumes", suffix=Path(filename).suffix, content=content)
        file_row = CandidateFile(
            org_id="default",
            candidate_id=candidate.id,
            original_name=filename,
            stored_path=store.path_for(key).as_posix(),
            storage_key=key,
            sha256=hashlib.sha256(content).hexdigest(),
            content_type="text/plain",
        )
        db.add(file_row)
        db.flush()
        version = ResumeVersion(
            candidate_id=candidate.id,
            file_id=file_row.id,
            version_no=1,
            status="PENDING",
            parser_version="resume-parser-1",
            quality_json="{}",
        )
        db.add(version)
        db.flush()
        task = TaskService(db).create(
            org_id="default",
            task_type="RESUME_PARSE",
            aggregate_type="candidate",
            aggregate_id=candidate.id,
        )
        db.commit()
        result = (task.id, candidate.id, version.id, file_row.id)
        db.close()
        return result

    def _run_handler(self, task_id, candidate_id, version_id, file_id):
        from app.infrastructure.messaging.envelope import build_envelope, parse_envelope
        from app.workers.resume_worker import handle_resume_parse_requested

        envelope = parse_envelope(
            json.dumps(
                build_envelope(
                    event_id="evt-x",
                    event_type="resume.parse.requested",
                    aggregate_type="candidate",
                    aggregate_id=candidate_id,
                    org_id="default",
                    trace_id="test",
                    payload={
                        "task_id": task_id,
                        "file_id": file_id,
                        "resume_version_id": version_id,
                    },
                )
            )
        )
        db = self.session_factory()
        try:
            handle_resume_parse_requested(db, envelope)
        finally:
            db.close()

    def test_parses_txt_resume_and_marks_parsed(self):
        task_id, candidate_id, version_id, file_id = self._create_import_entities(
            content="姓名：李四\n邮箱 li@example.com\n电话 13800138000\n技能：Python FastAPI\n3 年经验".encode()
        )

        self._run_handler(task_id, candidate_id, version_id, file_id)

        from app.infrastructure.models import Candidate, ParsedProfile, ResumeBlock, ResumeVersion, Task

        db = self.session_factory()
        task = db.get(Task, task_id)
        candidate = db.get(Candidate, candidate_id)
        version = db.get(ResumeVersion, version_id)
        profile = db.scalar(select(ParsedProfile).where(ParsedProfile.resume_version_id == version_id))
        blocks = list(db.scalars(select(ResumeBlock).where(ResumeBlock.resume_version_id == version_id)).all())
        db.close()

        self.assertEqual(task.status, "SUCCEEDED")
        self.assertEqual(candidate.status, "PARSED")
        self.assertEqual(version.status, "PARSED")
        self.assertIsNotNone(profile)
        self.assertGreater(len(blocks), 0)

    def test_short_resume_enters_needs_review(self):
        task_id, candidate_id, version_id, file_id = self._create_import_entities(content="张三".encode())

        self._run_handler(task_id, candidate_id, version_id, file_id)

        from app.infrastructure.models import Candidate, ResumeVersion, Task

        db = self.session_factory()
        task = db.get(Task, task_id)
        candidate = db.get(Candidate, candidate_id)
        version = db.get(ResumeVersion, version_id)
        db.close()

        self.assertEqual(task.status, "NEEDS_REVIEW")
        self.assertEqual(candidate.status, "NEEDS_REVIEW")
        self.assertEqual(version.status, "NEEDS_REVIEW")

    def test_image_resume_requires_ocr(self):
        # 伪造 PNG 头（无真实图像内容，仅验证路由到 OCR 判定）
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        task_id, candidate_id, version_id, file_id = self._create_import_entities(
            filename="scan.png", content=fake_png
        )

        self._run_handler(task_id, candidate_id, version_id, file_id)

        from app.infrastructure.models import Candidate, ResumeVersion, Task

        db = self.session_factory()
        task = db.get(Task, task_id)
        candidate = db.get(Candidate, candidate_id)
        version = db.get(ResumeVersion, version_id)
        db.close()

        self.assertEqual(task.status, "NEEDS_REVIEW")
        self.assertEqual(task.last_error_code, "OCR_REQUIRED")
        self.assertEqual(version.status, "NEEDS_OCR")
        self.assertEqual(candidate.status, "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
