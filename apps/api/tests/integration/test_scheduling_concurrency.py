"""排期并发集成测试：同一时段只允许一个活动预约（AC-008）。"""
import threading
from datetime import UTC, datetime, timedelta

import pytest
from app.domains.scheduling.service import (
    SchedulingError,
    create_schedule_invitation,
    create_slots,
    reserve_slot,
)
from app.infrastructure import models  # noqa: F401
from app.infrastructure.db import Base, SessionLocal, engine
from app.infrastructure.models import Candidate, Job, Reservation


@pytest.fixture(scope="module", autouse=True)
def schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


def make_environment(db, *, org_id="org-a"):
    job = Job(org_id=org_id, title="岗位", description="d", skills_json="[]")
    db.add(job)
    db.flush()
    candidate = Candidate(org_id=org_id, job_id=job.id, name="候选人")
    db.add(candidate)
    db.commit()

    starts = datetime.now(UTC) + timedelta(days=1)
    slots = create_slots(
        db,
        org_id=org_id,
        resource_type="interviewer",
        resource_id="iv-1",
        intervals=[(starts, starts + timedelta(minutes=30))],
    )
    invitation, token = create_schedule_invitation(db, org_id=org_id, candidate_id=candidate.id)
    return slots[0], token


def invitation_candidate_id(db) -> str:
    from app.infrastructure.models import ScheduleInvitation

    return db.query(ScheduleInvitation).first().candidate_id


class TestScheduling:
    def test_same_client_request_id_returns_original_reservation(self, db):
        slot, token = make_environment(db)

        first = reserve_slot(db, token=token, slot_id=slot.id, client_request_id="req-repeat-1")
        second = reserve_slot(db, token=token, slot_id=slot.id, client_request_id="req-repeat-1")

        assert second.id == first.id

    def test_used_invitation_cannot_reserve_another_slot(self, db):
        slot, token = make_environment(db)
        second_slot = create_slots(
            db,
            org_id="org-a",
            resource_type="interviewer",
            resource_id="iv-2",
            intervals=[(slot.starts_at + timedelta(hours=1), slot.ends_at + timedelta(hours=1))],
        )[0]
        reserve_slot(db, token=token, slot_id=slot.id, client_request_id="req-used-1")

        with pytest.raises(SchedulingError, match="INVITATION_USED"):
            reserve_slot(db, token=token, slot_id=second_slot.id, client_request_id="req-used-2")

    def test_reserve_then_cancel_then_reserve_again(self, db):
        slot, token = make_environment(db)

        first = reserve_slot(db, token=token, slot_id=slot.id, client_request_id="req-0001")
        assert first.status == "ACTIVE"

        from app.domains.scheduling.service import cancel_reservation

        cancelled = cancel_reservation(db, reservation_id=first.id, org_id="org-a", reason="时间冲突")
        assert cancelled.status == "CANCELLED"
        assert cancelled.active_slot_key is None

        # 取消后时段释放，可再次预约（新邀请）
        invitation2, token2 = create_schedule_invitation(db, org_id="org-a", candidate_id=invitation_candidate_id(db))
        db.expire_all()
        slot = db.get(models.ScheduleSlot, slot.id)
        second = reserve_slot(db, token=token2, slot_id=slot.id, client_request_id="req-0002")
        assert second.status == "ACTIVE"

    def test_concurrent_reservations_only_one_wins(self, db):
        slot, token = make_environment(db)
        results: list[str] = []
        lock = threading.Lock()

        def attempt(request_id: str):
            session = SessionLocal()
            try:
                try:
                    reserve_slot(session, token=token, slot_id=slot.id, client_request_id=request_id)
                    outcome = "OK"
                except SchedulingError:
                    # 并发下失败方可能命中唯一约束或时段状态检查，均为冲突语义
                    outcome = "CONFLICT"
                with lock:
                    results.append(outcome)
            finally:
                session.close()

        threads = [threading.Thread(target=attempt, args=(f"req-concurrent-{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(results) == ["CONFLICT", "OK"]

        db.expire_all()
        active = db.query(Reservation).filter_by(slot_id=slot.id, status="ACTIVE").count()
        assert active == 1

    def test_expired_invitation_rejected(self, db):
        from datetime import timedelta

        from app.infrastructure.models import now_utc

        slot, token = make_environment(db)
        from app.infrastructure.models import ScheduleInvitation

        invitation = db.query(ScheduleInvitation).order_by(ScheduleInvitation.created_at.desc()).first()
        invitation.expires_at = now_utc() - timedelta(seconds=5)
        db.commit()

        with pytest.raises(SchedulingError):
            reserve_slot(db, token=token, slot_id=slot.id, client_request_id="req-expired")

    def test_slot_overlap_rejected(self, db):
        starts = datetime.now(UTC) + timedelta(days=2)
        from app.domains.scheduling.service import SchedulingError, create_slots

        create_slots(
            db, org_id="org-a", resource_type="interviewer", resource_id="iv-2",
            intervals=[(starts, starts + timedelta(hours=1))],
        )
        with pytest.raises(SchedulingError):
            create_slots(
                db, org_id="org-a", resource_type="interviewer", resource_id="iv-2",
                intervals=[(starts + timedelta(minutes=30), starts + timedelta(minutes=90))],
            )
