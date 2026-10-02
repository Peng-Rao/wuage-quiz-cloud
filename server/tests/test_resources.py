"""资源边界回归：分页只实例化当前页，相似题只加载命中题，上传与渲染分批处理。"""

import asyncio
import hashlib
import json
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import event, inspect, select
from starlette.requests import ClientDisconnect

from app import similar
from app.api import files as files_api
from app.bank import BankFilter, compute_file_hash, search_questions
from app.config import get_settings
from app.compose import compose
from app.db import BankQuestion, ParseJob, SessionLocal, UploadOwner, User
from app.pipeline import run as pipeline
from app.pipeline.files import render_pages
from app.schemas import ComposeMessage, ComposeRequest
from app.storage import get_store, new_upload_key

from .fixtures import make_exam_pdf
from .test_api import client  # noqa: F401


@pytest.fixture(scope="module")
def bank_data():
    school = "resources-" + uuid.uuid4().hex[:8]
    job_id = uuid.uuid4().hex[:16]
    with SessionLocal() as s:
        s.add(ParseJob(id=job_id, school_id=school, file_name="资源回归.pdf", file_count=1, file_size=1,
                       file_type="pdf", file_keys=[], options={}, status="done", progress=100, stages=[],
                       meta={"subject": "数学"}, warnings=[]))
        for i in range(603):
            s.add(BankQuestion(id=f"{job_id}-{i}", school_id=school, source_job_id=job_id,
                               source_draft_id=f"{job_id}-draft-{i}", source_no=i,
                               type="单选题", score=5, coef=0.3, stem=f"{'目标' if i % 3 == 0 else '其他'}题目{i}",
                               options=[], meta={"grade": "高一", "stage": "高中", "subject": "数学"},
                               knowledge_points=[], images=[],
                               answer="答案", analysis="较长解析" * 2000, embedding=[0.1] * 256))
        s.commit()
    return school, job_id


@pytest.mark.parametrize("query,offset,total", [(None, 201, 603), ("目标", 199, 201), ("目标", 300, 201)])
def test_bank_only_loads_page_objects(bank_data, query, offset, total):
    school, job_id = bank_data
    with SessionLocal() as s:
        loaded = []
        event.listen(s, "loaded_as_persistent", lambda session, obj: loaded.append(obj.id)
                     if isinstance(obj, BankQuestion) else None)
        page, count = search_questions(s, school, BankFilter(q=query, grade="高一"), limit=5, offset=offset)
        expected = list(range(0, 603, 3)) if query else list(range(603))
        assert count == total
        assert [q.id for q in page] == [f"{job_id}-{i}" for i in expected[offset:offset + 5]]
        assert len(loaded) == len(page) <= 5
        assert all("embedding" in inspect(q).unloaded for q in page)


def test_similar_only_loads_winners_and_matches_full_sort(bank_data):
    school, _ = bank_data
    with SessionLocal() as s:
        rows = s.execute(select(BankQuestion.id, BankQuestion.stem).where(BankQuestion.school_id == school)).all()
        expected = sorted([(i, round(similar.lexical("目标题目123", text), 4)) for i, text in rows],
                          key=lambda item: item[1], reverse=True)[:5]
        loaded = []
        event.listen(s, "loaded_as_persistent", lambda session, obj: loaded.append(obj.id)
                     if isinstance(obj, BankQuestion) else None)
        result = similar.search(s, school, "目标题目123", limit=5, min_score=0)
        assert [(m.id, m.score) for m in result] == expected
        assert len(loaded) == 5
        assert all(m.row.answer == "答案" and "embedding" in inspect(m.row).unloaded for m in result)


async def test_compose_only_loads_selected_question_objects(bank_data):
    school, _ = bank_data
    with SessionLocal() as s:
        loaded = []
        event.listen(s, "loaded_as_persistent", lambda session, obj: loaded.append(obj.id)
                     if isinstance(obj, BankQuestion) else None)
        result = await compose(s, school, ComposeRequest(
            stage="高中", subject="数学", total=100,
            messages=[ComposeMessage(role="user", content="出一份练习卷")],
        ))
        items = [item for section in result.sections for item in section.items]
        assert 0 < len(items) <= 40
        assert len(loaded) == len(items)
        assert sum(item.score for item in items) == result.total == 100
        assert all(item.question.answer == "答案" and item.question.analysis for item in items)


def test_feature_cache_is_bounded_and_long_questions_are_not_cached():
    similar._cached_features.cache_clear()
    for i in range(1000):
        similar.lexical("查询题", f"候选题{i}")
    assert similar._cached_features.cache_info().currsize <= similar.FEATURE_CACHE_SIZE
    before = similar._cached_features.cache_info().currsize
    assert similar.lexical("很长题目" * 1000, "很长题目" * 1000) == pytest.approx(1)
    assert similar._cached_features.cache_info().currsize == before


@pytest.mark.parametrize("mode", ["success", "oversize", "empty", "disconnect", "cancel", "race"])
async def test_upload_stream_cleanup_and_atomic_publish(mode, monkeypatch):
    store = get_store()
    key = new_upload_key("resource.pdf")
    monkeypatch.setattr(get_settings(), "max_file_mb", 1)

    class RequestStream:
        async def stream(self):
            if mode == "empty":
                return
            for i in range(3):
                yield b"x" * (512 * 1024 if mode == "oversize" else 64 * 1024)
                if i == 0:
                    # 第一块在接收第二块之前已写盘，没有完整文件缓冲。
                    parts = list(store.path(key).parent.glob(f".{store.path(key).name}.*.part"))
                    assert len(parts) == 1 and parts[0].stat().st_size > 0
                    assert not store.exists(key)
                    if mode == "disconnect":
                        raise ClientDisconnect()
                    if mode == "cancel":
                        raise asyncio.CancelledError()
                    if mode == "race":
                        store.put(key, b"another upload")

    with SessionLocal() as s:
        user = s.get(User, "test-admin")
        s.add(UploadOwner(key=key, user_id=user.id))
        s.commit()
        if mode in ("oversize", "empty", "race"):
            with pytest.raises(HTTPException) as error:
                await files_api.put_file(key, RequestStream(), user, s)
            assert error.value.status_code == {"oversize": 413, "empty": 400, "race": 409}[mode]
        elif mode in ("disconnect", "cancel"):
            with pytest.raises(ClientDisconnect if mode == "disconnect" else asyncio.CancelledError):
                await files_api.put_file(key, RequestStream(), user, s)
        else:
            assert (await files_api.put_file(key, RequestStream(), user, s)).status_code == 204
    assert not list(store.path(key).parent.glob(f".{store.path(key).name}.*.part"))
    if mode == "success":
        assert store.get(key) == b"x" * (3 * 64 * 1024)
    elif mode == "race":
        assert store.get(key) == b"another upload"
    else:
        assert not store.exists(key)


def test_local_file_hash_does_not_read_whole_file(monkeypatch):
    store = get_store()
    keys = [new_upload_key("hash.pdf") for _ in range(2)]
    data = [b"abc" * 100000, b"second"]
    for key, value in zip(keys, data):
        store.put(key, value)
    monkeypatch.setattr(store, "get", lambda key: pytest.fail("本地指纹不能整文件读入内存"))
    expected = hashlib.sha256(b"".join(hashlib.sha256(value).digest() for value in data)).hexdigest()
    assert compute_file_hash(keys) == expected


def test_render_page_range_preserves_order():
    pdf = make_exam_pdf()
    all_pages = render_pages(pdf, 30, 0, 3)
    assert len(all_pages) == 3 and all(p.startswith(b"\x89PNG") for p in all_pages)
    assert render_pages(pdf, 30, 1, 2) == all_pages[1:2]
    assert render_pages(pdf, 30, 2, 99) == all_pages[2:]


def test_pipeline_renders_in_bounded_batches(client, monkeypatch):
    import pymupdf
    from .test_api import upload, wait_done

    with pymupdf.open(stream=make_exam_pdf(), filetype="pdf") as src, pymupdf.open() as doc:
        for _ in range(3):
            doc.insert_pdf(src)
        pdf = doc.tobytes()
    calls = []
    original = pipeline.render_pages

    def record(pdf, dpi, start, stop):
        calls.append((start, stop))
        return original(pdf, dpi, start, stop)

    monkeypatch.setattr(pipeline, "render_pages", record)
    key = upload(client, "分批页面.pdf", pdf)
    job = client.post("/api/parse-jobs", json={"fileKeys": [key], "fileNames": ["分批页面.pdf"],
                                               "options": {"dedupe": False}}).json()
    assert wait_done(client, job["id"])["status"] == "done"
    assert calls == [(0, 4), (4, 8), (8, 12)]
    assert all(get_store().exists(f"jobs/{job['id']}/pages/{i}.png") for i in range(1, 10))


def test_cancelled_sse_emits_one_final_snapshot(client):
    with SessionLocal() as s:
        job = ParseJob(id=uuid.uuid4().hex[:16], school_id="demo", file_name="取消订阅.pdf", file_count=1,
                       file_size=1, file_type="pdf", file_keys=[], options={}, status="cancelled", progress=0,
                       stages=[], meta={}, warnings=[])
        s.add(job)
        s.commit()
    with client.stream("GET", f"/api/parse-jobs/{job.id}/events") as response:
        snapshots = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    assert len(snapshots) == 1 and snapshots[0]["status"] == "cancelled"
