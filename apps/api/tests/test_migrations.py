"""TASK-001 迁移基线测试：空库升级、基线数据副本升级与版本可追溯。"""
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, select

APPS_API = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "jobs",
    "candidates",
    "candidate_files",
    "tasks",
    "match_results",
    "knowledge_documents",
    "knowledge_versions",
    "knowledge_chunks",
    "knowledge_ingestion_tasks",
    "assistant_query_logs",
    "assistant_citations",
    "organizations",
    "users",
    "roles",
    "role_bindings",
    "sessions",
    "audit_logs",
    "candidate_assignments",
    "resume_versions",
    "resume_blocks",
    "parsed_profiles",
    "job_versions",
    "rubric_versions",
    "analysis_runs",
    "agent_runs",
    "routing_decisions",
    "model_runs",
    "prompt_versions",
    "retrieval_runs",
    "questionnaires",
    "questionnaire_versions",
    "questionnaire_invitations",
    "questionnaire_responses",
    "schedule_slots",
    "schedule_invitations",
    "reservations",
    "notifications",
    "notification_deliveries",
    "interview_sessions",
    "interview_tokens",
    "interview_messages",
    "interview_reports",
    "alembic_version",
}

_TS = datetime(2026, 9, 13, tzinfo=UTC)


def make_config(db_url: str) -> Config:
    cfg = Config(str(APPS_API / "alembic.ini"))
    cfg.set_main_option("script_location", str(APPS_API / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def table_names(db_url: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def table(engine, name: str) -> Table:
    meta = MetaData()
    return Table(name, meta, autoload_with=engine)


def current_revision(db_url: str) -> str:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            alembic_version = table(engine, "alembic_version")
            return conn.scalar(select(alembic_version.c.version_num))
    finally:
        engine.dispose()


class TestMigrations:
    def test_empty_database_upgrades_to_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'empty.db'}"

            command.upgrade(make_config(db_url), "head")

            assert EXPECTED_TABLES.issubset(table_names(db_url))
            assert current_revision(db_url) == "0012_model_run_org"

    def test_baseline_data_copy_upgrades_without_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'baseline.db'}"
            cfg = make_config(db_url)

            # 第一步：升级到基线版本，模拟“迁移前的旧 schema”，并写入基线数据
            command.upgrade(cfg, "0001_baseline")
            engine = create_engine(db_url)
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "INSERT INTO jobs (id, title, description, skills_json, version, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("job-1", "迁移保留岗位", "描述", '["Python"]', 1, "OPEN", _TS.isoformat()),
                )
                conn.exec_driver_sql(
                    "INSERT INTO candidates (id, job_id, name, status, created_at) VALUES (?, ?, ?, ?, ?)",
                    ("cand-1", "job-1", "张三", "IMPORTED", _TS.isoformat()),
                )

            # 第二步：升级到 head，验证数据保留且新基础字段被回填
            command.upgrade(cfg, "head")

            try:
                jobs = table(engine, "jobs")
                candidates = table(engine, "candidates")
                with engine.connect() as conn:
                    title = conn.scalar(select(jobs.c.title).where(jobs.c.id == "job-1"))
                    status = conn.scalar(select(jobs.c.status).where(jobs.c.id == "job-1"))
                    job_updated_at = conn.scalar(select(jobs.c.updated_at).where(jobs.c.id == "job-1"))
                    job_org_id = conn.scalar(select(jobs.c.org_id).where(jobs.c.id == "job-1"))
                    name = conn.scalar(select(candidates.c.name).where(candidates.c.id == "cand-1"))
                    candidate_updated_at = conn.scalar(
                        select(candidates.c.updated_at).where(candidates.c.id == "cand-1")
                    )
                assert title == "迁移保留岗位"
                assert status == "OPEN"
                assert job_updated_at is not None
                assert job_org_id == "default"
                assert name == "张三"
                assert candidate_updated_at is not None
            finally:
                engine.dispose()

    def test_downgrade_to_baseline_removes_expanded_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'downgrade.db'}"
            cfg = make_config(db_url)

            command.upgrade(cfg, "head")
            command.downgrade(cfg, "0001_baseline")

            engine = create_engine(db_url)
            try:
                columns = {col["name"] for col in inspect(engine).get_columns("jobs")}
                assert "updated_at" not in columns
            finally:
                engine.dispose()
