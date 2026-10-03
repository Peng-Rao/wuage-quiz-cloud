"""原卷 PDF 预览：原始字节、本地 / 对象存储及整卷访问权限。"""
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.db import ParseJob, SessionLocal
from app.main import app
from app.storage import get_store

from .fixtures import make_exam_pdf
from .test_auth import accounts, data, sign_in  # noqa: F401
from .test_storage import cos_store, oss_store  # noqa: F401


@pytest.fixture
def pdf_paper(data):
    job_id = data["math"]
    key = f"uploads/{job_id}/paper.pdf"
    pdf = make_exam_pdf()
    get_store().put(key, pdf)
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        original = {k: getattr(job, k) for k in ["file_name", "file_keys", "file_type", "status"]}
        job.file_name = "数学期中试卷.pdf"
        job.file_keys = [{"key": key, "name": job.file_name}]
        s.commit()
    yield job_id, key, pdf
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        for field, value in original.items():
            setattr(job, field, value)
        s.commit()


def test_pdf_preview_local_and_permissions(accounts, data, pdf_paper):
    job_id, key, pdf = pdf_paper
    path = f"/api/parse-jobs/{job_id}/pdf"
    for role in ["admin", "leader"]:
        c = sign_in(accounts[role])
        r = c.get(path)
        assert r.status_code == 200 and r.content == pdf
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["content-disposition"] == f"inline; filename*=UTF-8''{quote('数学期中试卷.pdf', safe='')}"
        assert "no-store" in r.headers["cache-control"]
        # 文件路径仍不允许直接读取，必须经过任务权限校验。
        assert c.get(f"/api/files/{key}").status_code == 403
    assert TestClient(app).get(path).status_code == 401
    assert sign_in(accounts["member"]).get(path).status_code == 403
    assert sign_in(accounts["leader"]).get(f"/api/parse-jobs/{data['chem']}/pdf").status_code == 404
    assert sign_in(accounts["admin"]).get("/api/parse-jobs/missing/pdf").status_code == 404


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
def test_pdf_preview_before_parse_completes(accounts, pdf_paper, status):
    job_id, _, pdf = pdf_paper
    with SessionLocal() as s:
        s.get(ParseJob, job_id).status = status
        s.commit()
    r = sign_in(accounts["admin"]).get(f"/api/parse-jobs/{job_id}/pdf")
    assert r.status_code == 200 and r.content == pdf


@pytest.mark.parametrize("backend", ["oss_store", "cos_store"])
def test_pdf_preview_object_storage(accounts, pdf_paper, backend, request):
    job_id, key, pdf = pdf_paper
    store = request.getfixturevalue(backend)
    store.put(key, pdf)
    r = sign_in(accounts["leader"]).get(f"/api/parse-jobs/{job_id}/pdf", follow_redirects=False)
    assert r.status_code == 200 and r.content == pdf
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline;")


@pytest.mark.parametrize("kind", ["docx", "image"])
def test_pdf_preview_rejects_non_pdf(accounts, pdf_paper, kind):
    job_id, _, _ = pdf_paper
    with SessionLocal() as s:
        s.get(ParseJob, job_id).file_type = kind
        s.commit()
    r = sign_in(accounts["admin"]).get(f"/api/parse-jobs/{job_id}/pdf")
    assert r.status_code == 400


@pytest.mark.parametrize("keys", [[], [{"key": "uploads/missing.pdf", "name": "missing.pdf"}]])
def test_pdf_preview_missing_source(accounts, pdf_paper, keys):
    job_id, _, _ = pdf_paper
    with SessionLocal() as s:
        s.get(ParseJob, job_id).file_keys = keys
        s.commit()
    r = sign_in(accounts["admin"]).get(f"/api/parse-jobs/{job_id}/pdf")
    assert r.status_code == 404
