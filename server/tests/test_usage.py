import json

import httpx
import pytest
import respx
from sqlalchemy import delete, select

from app.config import Settings
from app.db import AiUsage, SessionLocal
from app.pipeline import llm
from app.pipeline.llm import Usage
from app.pipeline.parsers.base import SourceFile
from app.pipeline.parsers.mineru_cloud import MinerUCloudParser
from app.usage import cost_of, current_job, estimate_tokens, summarize

from .test_parsers import BASE, UPLOAD, ZIP, _zip

JOB = "usagejob0000001"


@pytest.fixture(autouse=True)
def _clean():
    with SessionLocal() as s:
        s.execute(delete(AiUsage).where(AiUsage.job_id == JOB))
        s.commit()
    token = current_job.set(JOB)
    yield
    current_job.reset(token)


def rows() -> list[AiUsage]:
    with SessionLocal() as s:
        return list(s.scalars(select(AiUsage).where(AiUsage.job_id == JOB).order_by(AiUsage.id)))


def settings(**kw) -> Settings:  # noqa: ANN003
    return Settings(llm_base_url="https://llm.test/v1", llm_api_key="k", llm_model="qwen-test", **kw)


def sse(content: str, usage: dict | None) -> httpx.Response:
    events = [{"choices": [{"delta": {"content": content}}]}]
    if usage:  # include_usage 时最后一个数据块 choices 为空、携带 usage
        events.append({"choices": [], "usage": usage})
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


QWEN_USAGE = {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500,
              "prompt_tokens_details": {"cached_tokens": 200}, "completion_tokens_details": {"reasoning_tokens": 120}}


def test_usage_parse_variants():
    assert Usage.parse(QWEN_USAGE) == Usage(1200, 300, 120, 200)
    deepseek = {"prompt_tokens": 50, "completion_tokens": 10, "prompt_cache_hit_tokens": 30}
    assert Usage.parse(deepseek) == Usage(50, 10, 0, 30)
    assert Usage.parse(None) is None


def test_estimate_tokens():
    assert estimate_tokens("选择题") == 3
    assert estimate_tokens("abcdefgh") == 2


async def test_llm_usage_recorded_from_stream(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return sse('{"ok": 1}', QWEN_USAGE)

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handler))
    assert await llm.chat_json("系统", "用户", settings(), purpose="segment") == {"ok": 1}
    assert seen["stream_options"] == {"include_usage": True}
    [r] = rows()
    assert (r.provider, r.purpose, r.model, r.status, r.estimated) == ("llm", "segment", "qwen-test", "ok", False)
    assert (r.prompt_tokens, r.completion_tokens, r.reasoning_tokens, r.cached_tokens) == (1200, 300, 120, 200)


async def test_llm_usage_estimated_when_missing(monkeypatch):
    monkeypatch.setattr(llm, "transport", httpx.MockTransport(lambda _: sse('{"ok": 1}', None)))
    await llm.chat_json("系统提示", "用户输入", settings(), purpose="classify")
    [r] = rows()
    assert r.estimated and r.prompt_tokens == estimate_tokens("系统提示用户输入") and r.completion_tokens > 0


async def test_failed_attempts_are_recorded(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("Server disconnected", request=req)

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handler))
    with pytest.raises(llm.LLMError):
        await llm.chat_json("系统", "用户", settings(), purpose="segment")
    rs = rows()
    assert len(rs) == 2 and all(r.status == "error" and r.estimated and r.prompt_tokens > 0 for r in rs)


def _row(**kw) -> AiUsage:  # noqa: ANN003
    base = dict(provider="llm", purpose="segment", model="qwen-test", prompt_tokens=0, completion_tokens=0,
                reasoning_tokens=0, cached_tokens=0, pages=0, duration_ms=0, estimated=False, status="ok")
    return AiUsage(**{**base, **kw})


def test_cost_calculation():
    s = settings(llm_prices={"qwen-test": {"input": 2, "output": 8, "cached_input": 0.5}}, mineru_price_per_page=0.1)
    r = _row(prompt_tokens=1_000_000, cached_tokens=200_000, completion_tokens=100_000)
    # 80 万 × 2 + 20 万 × 0.5 + 10 万 × 8，单位：元 / 百万
    assert cost_of(r, s) == pytest.approx(1.6 + 0.1 + 0.8)
    assert cost_of(_row(provider="mineru", model="mineru-vlm", pages=9), s) == pytest.approx(0.9)
    assert cost_of(_row(model="other"), s) is None
    # 未配置缓存单价时按输入单价计
    s2 = settings(llm_prices={"qwen-test": {"input": 2, "output": 8}})
    assert cost_of(_row(prompt_tokens=1_000_000, cached_tokens=500_000), s2) == pytest.approx(2)


def test_summary_partial_pricing():
    s = settings(llm_prices={"qwen-test": {"input": 2, "output": 8}})
    sm = summarize([
        _row(prompt_tokens=1000, completion_tokens=500, reasoning_tokens=100),
        _row(prompt_tokens=10, status="error", estimated=True),
        _row(provider="mineru", model="mineru-vlm", pages=9),
    ], s)
    assert (sm.calls, sm.llm_calls, sm.errors, sm.pages) == (3, 2, 1, 9)
    assert (sm.prompt_tokens, sm.completion_tokens, sm.total_tokens, sm.reasoning_tokens) == (1010, 500, 1510, 100)
    assert sm.estimated and not sm.priced and sm.unpriced_models == ["MinerU"]
    assert sm.llm_cost == pytest.approx((1010 * 2 + 500 * 8) / 1e6) and sm.mineru_cost is None
    assert summarize([], s).cost is None


@respx.mock
async def test_mineru_pages_recorded_even_if_download_fails():
    respx.post(f"{BASE}/api/v4/file-urls/batch").respond(json={"code": 0, "data": {"batch_id": "u1", "file_urls": [UPLOAD]}})
    respx.put(UPLOAD).respond(200)
    respx.get(f"{BASE}/api/v4/extract-results/batch/u1").mock(side_effect=[
        httpx.Response(200, json={"code": 0, "data": {"extract_result": [
            {"state": "running", "extract_progress": {"extracted_pages": 3, "total_pages": 7}}]}}),
        httpx.Response(200, json={"code": 0, "data": {"extract_result": [{"state": "done", "full_zip_url": ZIP}]}}),
    ])
    respx.get(ZIP).mock(side_effect=httpx.ConnectError(""))
    p = MinerUCloudParser(Settings(mineru_token="t", mineru_base_url=BASE, mineru_poll_interval=0))
    with pytest.raises(Exception):
        await p.parse(SourceFile("a.pdf", b"%PDF", "pdf"), ocr=False)
    [r] = rows()
    assert (r.provider, r.pages, r.model) == ("mineru", 7, "mineru-vlm")


@respx.mock
async def test_mineru_success_records_once():
    respx.post(f"{BASE}/api/v4/file-urls/batch").respond(json={"code": 0, "data": {"batch_id": "u2", "file_urls": [UPLOAD]}})
    respx.put(UPLOAD).respond(200)
    respx.get(f"{BASE}/api/v4/extract-results/batch/u2").respond(
        json={"code": 0, "data": {"extract_result": [{"state": "done", "full_zip_url": ZIP}]}})
    respx.get(ZIP).respond(content=_zip())
    from .fixtures import make_exam_pdf
    p = MinerUCloudParser(Settings(mineru_token="t", mineru_base_url=BASE, mineru_poll_interval=0))
    await p.parse(SourceFile("a.pdf", make_exam_pdf(), "pdf"), ocr=False)
    [r] = rows()
    assert r.pages == 3  # 未返回页数时读取 PDF 页数
