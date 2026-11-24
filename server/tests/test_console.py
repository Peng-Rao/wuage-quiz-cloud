"""管理后台：邮箱登录、root 授权、试卷 / 用户管理、运行时配置与单价。"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import runtime_settings
from app.config import get_settings
from app.console import create_root, ensure_root_from_env
from app.db import (
    AiUsage, AppSetting, BankQuestion, ConsoleAccount, ConsoleAudit, ConsoleSession, DraftQuestion, LoginSession,
    LoginThrottle, ModelPrice, ParseJob, SessionLocal, User,
)
from app.main import app

from .test_bank import _paper

ROOT_PASSWORD = "Root-password-123"
PASSWORD = "Console-password-123"


@pytest.fixture(autouse=True)
def _clean():
    yield
    with SessionLocal() as s:
        s.execute(delete(ConsoleSession))
        s.execute(delete(ConsoleAccount))
        s.execute(delete(LoginThrottle))
        s.execute(delete(AppSetting))
        s.commit()
    runtime_settings.sync(force=True)


@pytest.fixture()
def root_client():
    email = f"root-{uuid.uuid4().hex[:6]}@example.com"
    create_root(email, ROOT_PASSWORD)
    return login(email, ROOT_PASSWORD)


def login(email: str, password: str) -> TestClient:
    client = TestClient(app)
    r = client.post("/api/admin/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return client


def operator(root_client: TestClient, permissions: list[str]) -> tuple[str, TestClient]:
    email = f"op-{uuid.uuid4().hex[:6]}@example.com"
    r = root_client.post("/api/admin/accounts", json={"email": email.upper(), "displayName": "运营",
                                                      "password": PASSWORD, "permissions": permissions})
    assert r.status_code == 201, r.text
    assert r.json()["email"] == email
    return r.json()["id"], login(email, PASSWORD)


def test_root_login_and_isolated_from_app_session(root_client):
    me = root_client.get("/api/admin/auth/me").json()
    assert me["isRoot"] and set(me["permissions"]) == {"exams", "users", "keys", "ai"}
    # 后台会话只发往 /api/admin，题库接口不认
    assert root_client.get("/api/auth/me").status_code == 401
    assert TestClient(app).get("/api/admin/auth/me").status_code == 401
    # 题库管理员不能访问后台
    app_admin = TestClient(app)
    assert app_admin.post("/api/auth/login", json={"username": "test-admin", "password": "test-password-123"}).status_code == 200
    assert app_admin.get("/api/admin/overview").status_code == 401
    bad = TestClient(app).post("/api/admin/auth/login", json={"email": me["email"], "password": "wrong-password"})
    assert bad.status_code == 401
    root_client.post("/api/admin/auth/logout")
    assert root_client.get("/api/admin/auth/me").status_code == 401


def test_root_grants_permissions_and_changes_revoke_sessions(root_client):
    op_id, op = operator(root_client, ["exams"])
    assert op.get("/api/admin/exams").status_code == 200
    assert op.get("/api/admin/settings").status_code == 403
    assert op.get("/api/admin/users").status_code == 403
    assert op.get("/api/admin/accounts").status_code == 403
    assert op.get("/api/admin/audit").status_code == 403
    assert op.get("/api/admin/overview").status_code == 200

    r = root_client.put(f"/api/admin/accounts/{op_id}", json={"displayName": "运营", "permissions": ["exams", "keys"]})
    assert r.status_code == 200 and r.json()["permissions"] == ["exams", "keys"]
    assert op.get("/api/admin/exams").status_code == 401  # 权限变化后需重新登录
    op = login(r.json()["email"], PASSWORD)
    assert op.get("/api/admin/settings").status_code == 200

    root_id = root_client.get("/api/admin/auth/me").json()["id"]
    assert root_client.put(f"/api/admin/accounts/{root_id}", json={"displayName": "R", "active": False}).status_code == 400
    assert root_client.delete(f"/api/admin/accounts/{root_id}").status_code == 400
    assert root_client.post("/api/admin/accounts", json={"email": r.json()["email"], "displayName": "x",
                                                         "password": PASSWORD}).status_code == 409
    assert root_client.post("/api/admin/accounts", json={"email": "not-an-email", "displayName": "x",
                                                         "password": PASSWORD}).status_code == 422
    assert root_client.delete(f"/api/admin/accounts/{op_id}").status_code == 204
    assert op.get("/api/admin/settings").status_code == 401
    actions = [a["action"] for a in root_client.get("/api/admin/audit").json()["items"]]
    assert {"account.create", "account.update", "account.delete"} <= set(actions)


def test_change_own_password(root_client):
    email = root_client.get("/api/admin/auth/me").json()["email"]
    other = login(email, ROOT_PASSWORD)
    assert root_client.put("/api/admin/auth/password", json={"current": "nope", "new": "New-password-123"}).status_code == 400
    assert root_client.put("/api/admin/auth/password", json={"current": ROOT_PASSWORD, "new": "New-password-123"}).status_code == 204
    assert root_client.get("/api/admin/auth/me").status_code == 200
    assert other.get("/api/admin/auth/me").status_code == 401
    login(email, "New-password-123")


def test_root_bootstrap_from_env(monkeypatch):
    monkeypatch.setattr(get_settings(), "console_root_email", "Boot@Example.com")
    monkeypatch.setattr(get_settings(), "console_root_password", "Boot-password-123")
    ensure_root_from_env()
    ensure_root_from_env()  # 已有 root 时不重复创建
    with SessionLocal() as s:
        roots = list(s.scalars(select(ConsoleAccount).where(ConsoleAccount.is_root.is_(True))))
    assert [r.email for r in roots] == ["boot@example.com"]
    login("boot@example.com", "Boot-password-123")


def test_runtime_settings_override_env_and_mask_secrets(root_client):
    st = get_settings()
    env_key = st.llm_api_key
    r = root_client.put("/api/admin/settings", json={"values": {
        "llm_api_key": "sk-console-secret-9876", "llm_model": "qwen-console", "llm_base_url": "https://llm.example/v1",
        "llm_extra_body": '{"enable_thinking": false}'}})
    assert r.status_code == 200, r.text
    fields = {f["key"]: f for f in r.json()}
    assert fields["llm_api_key"]["value"] == "sk-****9876" and fields["llm_api_key"]["source"] == "console"
    assert fields["llm_model"]["value"] == "qwen-console"
    assert st.llm_api_key == "sk-console-secret-9876" and st.llm_extra_body == {"enable_thinking": False}
    assert st.llm_enabled
    assert "sk-console-secret" not in r.text

    detail = root_client.get("/api/admin/audit?action=settings").json()["items"][0]["detail"]
    assert detail["llm_api_key"] == "已修改" and "sk-console" not in str(detail)

    # 其他进程：版本号变化后同步
    with SessionLocal() as s:
        s.execute(delete(AppSetting).where(AppSetting.key == "llm_model"))
        s.commit()
    runtime_settings.save({"llm_api_key": None}, "test")
    assert st.llm_api_key == env_key and st.llm_model == ""

    assert root_client.put("/api/admin/settings", json={"values": {"mineru_model_version": "bad"}}).status_code == 422
    assert root_client.put("/api/admin/settings", json={"values": {"llm_base_url": "ftp://x"}}).status_code == 422
    assert root_client.put("/api/admin/settings", json={"values": {"database_url": "x"}}).status_code == 422
    assert root_client.post("/api/admin/settings/test", json={"target": "vision"}).json()["ok"] is False


def test_exam_management(root_client):
    with SessionLocal() as s:
        job_id = _paper(s, {"subject": "数学", "stage": "高中", "title": "后台管理测试卷"},
                        [{"type": "单选题", "score": 5, "stem": "1+1=?", "coef": 0.3}], drafts=3)
        s.add(AiUsage(job_id=job_id, school_id="demo", provider="llm", purpose="segment", model="m",
                      prompt_tokens=10, completion_tokens=5, cost=0.5, currency="CNY"))
        s.commit()
    page = root_client.get("/api/admin/exams", params={"q": "后台管理测试"}).json()
    assert page["total"] == 1
    item = page["items"][0]
    assert (item["id"], item["subject"], item["questionCount"], item["bankCount"], item["calls"]) == (job_id, "数学", 3, 1, 1)
    assert root_client.get("/api/admin/exams", params={"q": "后台管理测试", "status": "failed"}).json()["total"] == 0

    detail = root_client.get(f"/api/admin/exams/{job_id}").json()
    assert len(detail["questions"]) == 3 and detail["usage"]["calls"] == 1
    assert root_client.get(f"/api/admin/exams/{job_id}/files/0").status_code == 404

    meta = {"title": "改名后的试卷", "stage": "高中", "subject": "物理"}
    assert root_client.put(f"/api/admin/exams/{job_id}/meta", json=meta).status_code == 200
    with SessionLocal() as s:
        bank = s.scalars(select(BankQuestion).where(BankQuestion.source_job_id == job_id)).one()
        assert bank.meta["subject"] == "物理"

    assert root_client.post(f"/api/admin/exams/{job_id}/retry").status_code == 400
    assert root_client.post(f"/api/admin/exams/{job_id}/unpublish").json() == {"removed": 1}

    with SessionLocal() as s:
        s.get(ParseJob, job_id).status = "running"
        s.commit()
    assert root_client.delete(f"/api/admin/exams/{job_id}").status_code == 409
    with SessionLocal() as s:
        s.get(ParseJob, job_id).status = "done"
        s.commit()
    assert root_client.delete(f"/api/admin/exams/{job_id}").status_code == 204
    with SessionLocal() as s:
        assert s.get(ParseJob, job_id) is None
        assert not s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id)).all()
        assert s.scalars(select(AiUsage).where(AiUsage.job_id == job_id)).all()  # 成本记录保留
        s.execute(delete(AiUsage).where(AiUsage.job_id == job_id))
        s.commit()


def test_user_management_and_presence(root_client):
    name = f"teacher-{uuid.uuid4().hex[:6]}"
    r = root_client.post("/api/admin/users", json={"username": name, "displayName": "张老师", "password": PASSWORD,
                                                   "role": "member", "subjects": ["数学"]})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    teacher = TestClient(app)
    assert teacher.post("/api/auth/login", json={"username": name, "password": PASSWORD}).status_code == 200
    row = next(u for u in root_client.get("/api/admin/users").json() if u["id"] == uid)
    assert row["isOnline"] and row["sessions"] == 1

    assert root_client.post(f"/api/admin/users/{uid}/sign-out").status_code == 204
    assert teacher.get("/api/auth/me").status_code == 401
    row = next(u for u in root_client.get("/api/admin/users").json() if u["id"] == uid)
    assert not row["isOnline"] and row["sessions"] == 0

    r = root_client.put(f"/api/admin/users/{uid}", json={"displayName": "张老师", "role": "leader", "subjects": ["数学", "物理"]})
    assert r.status_code == 200 and r.json()["role"] == "leader"
    assert root_client.delete(f"/api/admin/users/{uid}").status_code == 204
    with SessionLocal() as s:
        assert s.get(User, uid) is None
        assert not s.scalars(select(LoginSession).where(LoginSession.user_id == uid)).all()


def test_cannot_delete_last_app_admin(root_client):
    with SessionLocal() as s:
        admins = list(s.scalars(select(User).where(User.role == "admin", User.active.is_(True))))
        others = [a for a in admins if a.id != "test-admin"]
        for a in others:
            a.active = False
        s.commit()
    try:
        assert root_client.delete("/api/admin/users/test-admin").status_code == 400
    finally:
        with SessionLocal() as s:
            for a in others:
                s.get(User, a.id).active = True
            s.commit()


def test_manual_prices_and_recompute(root_client, monkeypatch):
    model = f"console-model-{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(get_settings(), "llm_prices", {model: {"input": 9, "output": 9}})
    with SessionLocal() as s:
        s.add(AiUsage(school_id="demo", provider="llm", purpose="other", model=model, prompt_tokens=1_000_000,
                      completion_tokens=1_000_000))
        s.commit()
    r = root_client.post("/api/admin/prices", json={
        "provider": "llm", "model": model, "currency": "CNY", "notes": "合同价",
        "tiers": [{"input": 2, "output": 8, "cached_input": 0.5}],
        "peak": {"ranges": [[9, 18]], "weekdays_only": True}})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "manual" and r.json()["notes"].startswith("管理后台")
    assert root_client.post("/api/admin/prices", json={"provider": "llm", "model": model, "currency": "CNY",
                                                       "tiers": []}).status_code == 422
    assert root_client.post("/api/admin/prices", json={"provider": "llm", "model": model, "currency": "CNY",
                                                       "tiers": [{"input": -1, "output": 1}]}).status_code == 422

    from app import pricing
    monkeypatch.setattr(pricing, "fetch_sources", lambda *a, **k: ({}, []))
    pricing.refresh()  # LLM_PRICES 不覆盖后台设置的单价
    with SessionLocal() as s:
        latest = s.scalars(select(ModelPrice).where(ModelPrice.model == model).order_by(ModelPrice.id.desc())).first()
        assert latest.tiers[0]["input"] == 2

    assert root_client.post("/api/admin/prices/recompute", json={}).status_code == 200
    with SessionLocal() as s:
        u = s.scalars(select(AiUsage).where(AiUsage.model == model)).one()
        assert u.currency == "CNY" and u.cost == pytest.approx(10.0)
    listed = [p for p in root_client.get("/api/admin/prices").json() if p["model"] == model]
    assert listed and listed[0]["billing"]
    assert root_client.get("/api/admin/prices/status").json()["billingSource"]
    assert root_client.get("/api/admin/usage/analysis").status_code == 200

    assert root_client.delete("/api/admin/prices/manual", params={"provider": "llm", "model": model}).status_code == 204
    assert root_client.delete("/api/admin/prices/manual", params={"provider": "llm", "model": model}).status_code == 404
    with SessionLocal() as s:
        s.execute(delete(AiUsage).where(AiUsage.model == model))
        s.execute(delete(ModelPrice).where(ModelPrice.model == model))
        s.commit()


def test_audit_never_contains_passwords(root_client):
    operator(root_client, ["users"])
    with SessionLocal() as s:
        dump = str([(a.action, a.target, a.detail) for a in s.scalars(select(ConsoleAudit))])
    assert PASSWORD not in dump and ROOT_PASSWORD not in dump
