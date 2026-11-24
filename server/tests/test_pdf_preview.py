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


def _image(color: tuple[float, float, float], fmt: str = "png") -> bytes:
    """渲染一页纯色小图（模拟页面图、拍照图）。"""
    import pymupdf
    with pymupdf.open() as doc:
        page = doc.new_page(width=40, height=60)
        page.draw_rect(page.rect, color=color, fill=color)
        return page.get_pixmap(alpha=False).tobytes(fmt)


def _png(color: tuple[float, float, float]) -> bytes:
    return _image(color)


def _pages(data: bytes) -> int:
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return doc.page_count


@pytest.fixture
def docx_paper(pdf_paper, monkeypatch):
    """把同一任务改成 Word 试卷：原始文件为 .docx，预览时转换为 PDF。"""
    job_id, _, pdf = pdf_paper
    key = f"uploads/{job_id}/paper.docx"
    store = get_store()
    store.put(key, b"PK fake docx")
    leftovers = [f"jobs/{job_id}/original.pdf", f"jobs/{job_id}/pages/1.png", f"jobs/{job_id}/pages/2.png"]
    saved = {k: store.get(k) for k in leftovers if store.exists(k)}  # 其他用例写入的占位页面图
    for k in saved:
        store.path(k).unlink()
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        job.file_type, job.file_name, job.page_count = "docx", "数学期中试卷.docx", 2
        job.file_keys = [{"key": key, "name": job.file_name}]
        s.commit()
    calls = []

    def convert(data: bytes):  # noqa: ANN202
        calls.append(data)
        return converted["pdf"]

    converted = {"pdf": pdf}
    monkeypatch.setattr("app.original_pdf.docx_to_pdf", convert)
    yield job_id, pdf, converted, calls
    for k in leftovers:
        path = store.path(k)
        if path.exists():
            path.unlink()
    for k, content in saved.items():
        store.put(k, content)


def test_corrupt_page_images_are_not_a_server_error(accounts, docx_paper):
    job_id, _, converted, _ = docx_paper
    converted["pdf"] = None
    get_store().put(f"jobs/{job_id}/pages/1.png", b"png")
    r = sign_in(accounts["admin"]).get(f"/api/parse-jobs/{job_id}/pdf")
    assert r.status_code == 404


def test_word_paper_is_converted_once_then_cached(accounts, docx_paper):
    job_id, pdf, _, calls = docx_paper
    c = sign_in(accounts["leader"])
    for _ in range(2):
        r = c.get(f"/api/parse-jobs/{job_id}/pdf")
        assert r.status_code == 200 and r.content == pdf
        assert r.headers["x-converted"] == "1"
        assert r.headers["content-disposition"] == f"inline; filename*=UTF-8''{quote('数学期中试卷.pdf', safe='')}"
    assert calls == [b"PK fake docx"]  # 第二次直接用保存的 PDF
    assert get_store().exists(f"jobs/{job_id}/original.pdf")


def test_word_paper_falls_back_to_page_images(accounts, docx_paper):
    job_id, _, converted, _ = docx_paper
    converted["pdf"] = None  # 未安装 LibreOffice 或转换失败
    r = sign_in(accounts["admin"]).get(f"/api/parse-jobs/{job_id}/pdf")
    assert r.status_code == 404 and "LibreOffice" in r.json()["message"]
    get_store().put(f"jobs/{job_id}/pages/1.png", _png((1, 1, 1)))
    get_store().put(f"jobs/{job_id}/pages/2.png", _png((0.9, 0.9, 0.9)))
    r = sign_in(accounts["admin"]).get(f"/api/parse-jobs/{job_id}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and _pages(r.content) == 2


def test_photo_paper_is_combined_into_one_pdf(accounts, pdf_paper):
    job_id, _, _ = pdf_paper
    store = get_store()
    keys = [f"uploads/{job_id}/p1.png", f"uploads/{job_id}/p2.jpg"]
    store.put(keys[0], _png((1, 1, 1)))
    store.put(keys[1], _image((0.5, 0.5, 0.5), "jpg"))
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        job.file_type, job.file_name = "image", "拍照试卷.png 等 2 个文件"
        job.file_keys = [{"key": keys[0], "name": "第1页.png"}, {"key": keys[1], "name": "第2页.jpg"}]
        s.commit()
    r = sign_in(accounts["admin"]).get(f"/api/parse-jobs/{job_id}/pdf")
    assert r.status_code == 200 and _pages(r.content) == 2 and r.headers["x-converted"] == "1"
    assert sign_in(accounts["member"]).get(f"/api/parse-jobs/{job_id}/pdf").status_code == 403


@pytest.mark.parametrize("keys", [[], [{"key": "uploads/missing.pdf", "name": "missing.pdf"}]])
def test_pdf_preview_missing_source(accounts, pdf_paper, keys):
    job_id, _, _ = pdf_paper
    with SessionLocal() as s:
        s.get(ParseJob, job_id).file_keys = keys
        s.commit()
    r = sign_in(accounts["admin"]).get(f"/api/parse-jobs/{job_id}/pdf")
    assert r.status_code == 404
