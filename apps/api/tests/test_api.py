import asyncio
import os
import tempfile
import unittest
from uuid import uuid4

import httpx


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = "sqlite:///./data/test.db"
        os.environ["UPLOAD_DIR"] = cls.temp_dir.name
        from app.infrastructure import models  # noqa: F401
        from app.infrastructure.db import Base, engine
        from app.main import app

        # 应用不再自动建表（TASK-001）；测试通过显式初始化 schema
        Base.metadata.create_all(bind=engine)
        cls.app = app

    @classmethod
    def tearDownClass(cls):
        from app.infrastructure.db import engine

        engine.dispose()
        cls.temp_dir.cleanup()

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        # 测试环境显式启用请求头模拟身份；默认 ADMIN/default 组织
        headers.setdefault("X-User-Role", "ADMIN")
        headers.setdefault("X-Org-Id", "default")

        async def run():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, url, headers=headers, **kwargs)

        return asyncio.run(run())

    def test_health_reports_ready(self):
        response = self.request("GET", "/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_creates_job_and_returns_version(self):
        response = self.request(
            "POST",
            "/api/v1/jobs",
            json={
                "title": "AI 应用开发工程师",
                "description": "负责大模型应用和 RAG 系统开发",
                "skills": ["Python", "FastAPI", "LangGraph"],
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()["data"]
        self.assertEqual(body["title"], "AI 应用开发工程师")
        self.assertEqual(body["versions"][0]["version_no"], 1)
        self.assertEqual(body["status"], "OPEN")

    def test_imports_resume_as_pending_task(self):
        job = self.request(
            "POST",
            "/api/v1/jobs",
            json={"title": "后端工程师", "description": "Python 后端开发", "skills": ["Python"]},
        ).json()["data"]
        response = self.request(
            "POST",
            "/api/v1/candidates/import",
            data={"job_id": job["id"]},
            files={"files": ("resume.txt", "张三\nPython FastAPI\n3 年经验".encode(), "text/plain")},
        )

        self.assertEqual(response.status_code, 202)
        body = response.json()["data"]
        item = body["imports"][0]
        self.assertFalse(item["reused"])
        self.assertTrue(item["candidate_id"])
        self.assertTrue(item["task_id"])

    def test_task_events_endpoint_rejects_unknown_task(self):
        response = self.request("GET", "/api/v1/tasks/no-such-task/events")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "TASK_NOT_FOUND")

    def create_indexed_knowledge_document(self, *, title, content, allowed_roles="HR"):
        uploaded = self.request(
            "POST",
            "/api/v1/knowledge/documents",
            data={"title": title, "allowed_roles": allowed_roles},
            files={"file": (f"{title}.txt", content.encode("utf-8"), "text/plain")},
            headers={"X-User-Role": "ADMIN", "X-Org-Id": "default"},
        )
        self.assertEqual(uploaded.status_code, 202)
        upload_data = uploaded.json()["data"]

        from app.workers.knowledge_worker import process_knowledge_task

        process_knowledge_task(upload_data["task_id"])
        return upload_data

    def test_knowledge_document_is_not_searchable_before_publish(self):
        marker = f"未发布制度条款{uuid4().hex}"
        document = self.create_indexed_knowledge_document(
            title="未发布制度",
            content=f"# 第一章\n{marker}只用于验证发布控制。",
        )

        task_response = self.request(
            "GET",
            f"/api/v1/knowledge/tasks/{document['task_id']}",
            headers={"X-User-Role": "ADMIN", "X-Org-Id": "default"},
        )
        response = self.request(
            "POST",
            "/api/v1/assistant/query",
            json={"question": marker},
            headers={"X-User-Role": "HR", "X-Org-Id": "default"},
        )

        self.assertEqual(task_response.status_code, 200)
        self.assertEqual(task_response.json()["data"]["status"], "DONE")
        self.assertEqual(task_response.json()["data"]["progress"], 100)
        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertFalse(body["reliable"])
        self.assertEqual(body["citations"], [])
        self.assertEqual(body["error_code"], "NO_RELIABLE_EVIDENCE")

    def test_published_knowledge_document_returns_cited_answer(self):
        marker = f"候选人背调授权码{uuid4().hex}"
        document = self.create_indexed_knowledge_document(
            title="候选人背调管理制度",
            content=f"# 授权要求\n{marker}要求 HR 在背调前取得候选人书面授权。",
        )
        published = self.request(
            "POST",
            f"/api/v1/knowledge/documents/{document['document_id']}/publish",
            headers={"X-User-Role": "ADMIN", "X-Org-Id": "default"},
        )
        self.assertEqual(published.status_code, 200)

        response = self.request(
            "POST",
            "/api/v1/assistant/query",
            json={"question": f"{marker}有什么要求？"},
            headers={"X-User-Role": "HR", "X-Org-Id": "default"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        # Provider 未配置时（PD-003）：返回授权证据与显式状态，不编造生成答案（AC-017）
        self.assertFalse(body["reliable"])
        self.assertEqual(body["error_code"], "MODEL_NOT_CONFIGURED")
        self.assertIn("书面授权", body["answer"])
        self.assertGreaterEqual(len(body["citations"]), 1)
        self.assertEqual(body["citations"][0]["document_title"], "候选人背调管理制度")
        self.assertEqual(body["citations"][0]["section"], "授权要求")

    def test_knowledge_retrieval_filters_disallowed_roles(self):
        marker = f"招聘预算密级词{uuid4().hex}"
        document = self.create_indexed_knowledge_document(
            title="招聘预算制度",
            content=f"# 预算审批\n{marker}仅允许 HR 查看。",
            allowed_roles="HR",
        )
        self.request(
            "POST",
            f"/api/v1/knowledge/documents/{document['document_id']}/publish",
            headers={"X-User-Role": "ADMIN", "X-Org-Id": "default"},
        )

        response = self.request(
            "POST",
            "/api/v1/assistant/query",
            json={"question": marker},
            headers={"X-User-Role": "INTERVIEWER", "X-Org-Id": "default"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()["data"]
        self.assertFalse(body["reliable"])
        self.assertEqual(body["citations"], [])


if __name__ == "__main__":
    unittest.main()
