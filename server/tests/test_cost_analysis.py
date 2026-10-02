from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.config import Settings
from app.cost_analysis import analyze
from app.db import AiUsage, DraftQuestion, ParseJob, SessionLocal, User
from app.main import app
from app.pricing import BEIJING
from app.usage import attribute, current_actor, current_job, record

SCHOOL = "cost-test"
JOB = "costjob00000001"
USER = "cost-user-0001"


def at(day: int, hour: int) -> datetime:
    """2026-03-<day> 北京时间 <hour> 点（3 月 2 日为周一）。"""
    return datetime(2026, 3, day, hour, tzinfo=BEIJING)


def usage(**kw) -> AiUsage:  # noqa: ANN003
    base = dict(school_id=SCHOOL, provider="llm", purpose="segment", model="m1", prompt_tokens=1000,
                completion_tokens=100, cost=1.0, currency="CNY", status="ok")
    return AiUsage(**{**base, **kw})


@pytest.fixture
def seeded():
    with SessionLocal() as s:
        s.add(User(id=USER, username="cost-user", display_name="王老师", password_hash="x", role="leader",
                   subjects=["数学"], active=True))
        s.add(ParseJob(id=JOB, school_id=SCHOOL, owner_id=USER, subject_scope="数学", file_name="期中卷.pdf",
                       file_count=1, file_size=1, file_type="pdf", file_keys=[], page_count=4, options={},
                       status="done", stages=[], meta={"subject": "物理"}))
        s.flush()
        s.add_all(DraftQuestion(id=f"{JOB}-{i}", job_id=JOB, no=i, type="单选题", score=3, page=1, stem="题",
                                coef=0.5, confidence=1) for i in range(1, 9))
        s.add_all([
            # 早期调用没有记录账号 / 学科：按任务补齐
            usage(job_id=JOB, created_at=at(2, 10), cost=2.0, cached_tokens=400),
            usage(job_id=JOB, provider="mineru", purpose="parse", model="mineru-vlm", prompt_tokens=0,
                  completion_tokens=0, pages=4, cost=0.4, created_at=at(2, 20)),
            usage(job_id=JOB, created_at=at(3, 11), cost=0.5, status="error"),
            # 组卷：无任务，记录了账号与学科
            usage(user_id=USER, subject="化学", purpose="compose", model="m2", created_at=at(4, 3), cost=1.5),
            # 其他货币、缺单价：不计入金额
            usage(purpose="similar", model="m3", created_at=at(4, 9), cost=0.1, currency="USD"),
            usage(purpose="similar", model="m3", created_at=at(4, 9), cost=None, currency=None),
            # 上一周期（2/27–3/1）
            usage(created_at=at(1, 12), cost=3.0),
            # 区间之外
            usage(created_at=at(5, 9), cost=100.0),
        ])
        s.commit()
    yield
    with SessionLocal() as s:
        s.execute(delete(AiUsage).where(AiUsage.school_id == SCHOOL))
        s.execute(delete(ParseJob).where(ParseJob.id == JOB))
        s.execute(delete(User).where(User.id == USER))
        s.commit()


def test_analyze(seeded):
    settings = Settings(llm_base_url="https://api.tokenhub.tencentmaas.com/v1", llm_prices={})
    with SessionLocal() as s:
        a = analyze(s, SCHOOL, date(2026, 3, 2), date(2026, 3, 4), settings)

    assert (a.days, a.previous_start, a.previous_end) == (3, "2026-02-27", "2026-03-01")
    assert a.summary.currency == "¥" and a.summary.cost == pytest.approx(4.4) and a.summary.calls == 6
    assert a.previous_cost == pytest.approx(3.0)
    assert a.avg_daily_cost == pytest.approx(4.4 / 3) and a.projected_monthly_cost == pytest.approx(44.0)
    assert [d.date for d in a.daily] == ["2026-03-02", "2026-03-03", "2026-03-04"]
    assert a.daily[0].llm_cost == pytest.approx(2.0) and a.daily[0].mineru_cost == pytest.approx(0.4)
    # 解析任务：2 + 0.4 + 0.5（失败调用同样计费）
    assert (a.jobs, a.pages, a.questions) == (1, 4, 8)
    assert a.cost_per_job == pytest.approx(2.9) and a.cost_per_question == pytest.approx(2.9 / 8)
    assert a.error_cost == pytest.approx(0.5)
    assert a.cache_hit_rate == pytest.approx(400 / 5000)

    assert [(b.key, b.cost, b.unpriced_calls) for b in a.by_purpose] == \
        [("segment", 2.5, 0), ("compose", 1.5, 0), ("parse", 0.4, 0), ("similar", 0, 2)]
    users = {b.key: (b.label, b.cost) for b in a.by_user}
    assert users[USER] == ("王老师", pytest.approx(4.4)) and users[""] == ("未关联账号", 0)
    # 任务学科取识别出的试卷学科（物理），其次上传时所选
    assert {b.label: b.cost for b in a.by_subject} == {"物理": pytest.approx(2.9), "化学": 1.5, "未识别学科": 0}
    assert next(b for b in a.by_model if b.key == "mineru-vlm").provider == "mineru"

    [job] = a.top_jobs
    assert (job.owner, job.subject, job.questions, job.calls, job.cost) == ("王老师", "物理", 8, 3, pytest.approx(2.9))

    # TokenHub：工作日 9–12、14–18 点为高峰；3/2 10 点与 3/3 11 点的调用为高峰
    assert a.billing_source == "tencent" and [h.hour for h in a.hourly if h.peak] == [9, 10, 11, 14, 15, 16, 17]
    assert a.hourly[10].cost == pytest.approx(2.0) and a.hourly[3].calls == 1
    assert a.peak_cost_share == pytest.approx(2.5 / 4.4, abs=1e-4)


def test_record_attributes_job_owner_and_actor(seeded):
    token = current_job.set(JOB)
    try:
        record("llm", "answer", "m1", prompt_tokens=10)
    finally:
        current_job.reset(token)
    actor = current_actor.set((None, None))
    try:
        attribute(USER, "化学")
        record("llm", "compose", "m1", prompt_tokens=10)
    finally:
        current_actor.reset(actor)
    with SessionLocal() as s:
        rows = s.query(AiUsage).filter(AiUsage.purpose.in_(["answer", "compose"]), AiUsage.user_id == USER,
                                       AiUsage.prompt_tokens == 10).order_by(AiUsage.id).all()
        assert [(r.purpose, r.subject) for r in rows] == [("answer", "物理"), ("compose", "化学")]
        for r in rows:
            s.delete(r)
        s.commit()


def test_analysis_api():
    with TestClient(app) as c:
        assert c.get("/api/usage/analysis").status_code == 401
        assert c.post("/api/auth/login", json={"username": "test-admin", "password": "test-password-123"}).status_code == 200
        r = c.get("/api/usage/analysis", params={"start": "2026-03-01", "end": "2026-03-07"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["days"] == 7 and len(body["daily"]) == 7 and len(body["hourly"]) == 24
        assert {"byPurpose", "byModel", "byUser", "bySubject", "topJobs", "projectedMonthlyCost"} <= body.keys()
        assert c.get("/api/usage/analysis").json()["days"] == 30
        assert c.get("/api/usage/analysis", params={"start": "2026-03-07", "end": "2026-03-01"}).status_code == 422
        assert c.get("/api/usage/analysis", params={"start": "2025-01-01", "end": "2026-03-01"}).status_code == 422
