"""大模型客户端：看图请求用 VISION_MODEL、Key 无效与额度用完直接报错、TLS 中断重试、格式修正与备用模型。"""

import json
import ssl

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.config import Settings
from app.db import AiUsage, SessionLocal
from app.pipeline import llm
from app.usage import current_job

from .llm_fakes import mock_llm, sse


def settings(**kw) -> Settings:  # noqa: ANN003
    base = dict(llm_base_url="https://llm.test/v1", llm_api_key="k", llm_model="text-model", vision_model="vl-model",
                llm_extra_body={})
    return Settings(**{**base, **kw})


def mock(monkeypatch, responses: list[tuple[int, str]]) -> list[dict]:
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        status, text = responses.pop(0)
        if status != 200:
            return httpx.Response(status, text=text)
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    mock_llm(monkeypatch, handler)
    return seen


async def test_vision_requests_use_vision_model(monkeypatch):
    seen = mock(monkeypatch, [(200, '{"answer": "B"}')])
    assert await llm.chat_json("sys", "题目", settings(), images=["https://img/1.png"]) == {"answer": "B"}
    assert seen[0]["model"] == "vl-model" and "response_format" not in seen[0]
    assert seen[0]["messages"][1]["content"][1] == {"type": "image_url", "image_url": {"url": "https://img/1.png"}}


@pytest.mark.parametrize("status,text,match", [
    (401, "Invalid API-key provided.", "Key 无效"),
    (403, '{"error":{"code":"AllocationQuota.FreeTierOnly","message":"The free tier has been exhausted."}}', "额度已用完"),
    (429, "insufficient_quota", "额度已用完"),
])
async def test_key_and_quota_errors_are_not_retried(monkeypatch, status, text, match):
    seen = mock(monkeypatch, [(status, text), (200, "{}")])
    with pytest.raises(llm.LLMError, match=match):
        await llm.chat_json("sys", "u", settings(), retries=1)
    assert len(seen) == 1


async def test_other_errors_are_retried(monkeypatch):
    seen = mock(monkeypatch, [(500, "boom"), (200, '{"ok": 1}')])
    assert await llm.chat_json("sys", "u", settings(), retries=1) == {"ok": 1}
    assert len(seen) == 2


async def test_tls_error_while_streaming_is_retried(monkeypatch):
    calls = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            class Broken(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield b'data: {"choices":[{"delta":{"content":"{"}}]}\n\n'
                    raise ssl.SSLError("[SSL: RECORD_LAYER_FAILURE] record layer failure")
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Broken())
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}]})

    mock_llm(monkeypatch, flaky)
    assert await llm.chat_json("sys", "u", settings(), retries=1) == {"ok": 1}
    assert calls["n"] == 2


# ---------------- LangChain：结构化输出、格式修正、备用模型、用量 ----------------


class Answer(BaseModel):
    answer: str
    score: int


async def test_invalid_json_is_repaired_by_reasking(monkeypatch):
    seen = mock(monkeypatch, [(200, "不是 JSON"), (200, '{"answer": "B", "score": "5"}')])
    out = await llm.structured([llm.SystemMessage(content="sys"), llm.HumanMessage(content="题目")], None, Answer,
                               settings(), purpose="answer")
    assert out == Answer(answer="B", score=5)
    # 第二次请求带上错误的回答和修正要求
    repair = seen[1]["messages"]
    assert repair[2] == {"role": "assistant", "content": "不是 JSON"} and "不符合要求" in repair[3]["content"]


async def test_schema_and_check_failures_reask_once_then_raise(monkeypatch):
    seen = mock(monkeypatch, [(200, '{"answer": "B"}'), (200, '{"answer": "C", "score": 1}')])

    def check(a: Answer) -> None:
        if a.answer not in "AB":
            raise ValueError("答案只能是 A 或 B")

    with pytest.raises(llm.LLMError, match="答案只能是 A 或 B"):
        await llm.structured([llm.HumanMessage(content="题目")], None, Answer, settings(), purpose="answer", check=check)
    assert len(seen) == 2 and "score" in seen[1]["messages"][-1]["content"]


async def test_prompt_template_values_are_filled(monkeypatch):
    seen = mock(monkeypatch, [(200, '{"answer": "A", "score": 2}')])
    prompt = llm.ChatPromptTemplate.from_messages([("system", "你是{subject}老师，输出 {{\"answer\": ...}}"), ("human", "{input}")])
    await llm.structured(prompt, {"subject": "数学", "input": "1+1"}, Answer, settings(), purpose="answer")
    assert seen[0]["messages"][0]["content"] == '你是数学老师，输出 {"answer": ...}'
    assert seen[0]["messages"][1]["content"] == "1+1"


async def test_transient_errors_back_off_then_fall_back_to_backup_model(monkeypatch):
    seen = mock(monkeypatch, [(503, "busy"), (502, "busy"), (500, "busy"), (200, '{"ok": 1}')])
    s = settings(llm_fallback_model="backup-model")
    assert await llm.chat_json("sys", "u", s) == {"ok": 1}
    assert [b["model"] for b in seen] == ["text-model"] * 3 + ["backup-model"]


async def test_rejected_request_switches_to_backup_without_retry(monkeypatch):
    seen = mock(monkeypatch, [(404, '{"error": {"message": "model not found"}}'), (200, '{"ok": 1}')])
    assert await llm.chat_json("sys", "u", settings(llm_fallback_model="backup-model")) == {"ok": 1}
    assert [b["model"] for b in seen] == ["text-model", "backup-model"]


async def test_invalid_key_is_not_retried_or_failed_over(monkeypatch):
    seen = mock(monkeypatch, [(401, "Invalid API-key provided."), (200, "{}")])
    with pytest.raises(llm.FatalLLMError, match="Key 无效"):
        await llm.chat_json("sys", "u", settings(llm_fallback_model="backup-model"))
    assert len(seen) == 1


async def test_vision_never_falls_back_to_text_model(monkeypatch):
    seen = mock(monkeypatch, [(500, "x")] * 3 + [(200, '{"ok": 1}')])
    with pytest.raises(llm.LLMError):
        await llm.chat_json("sys", "u", settings(llm_fallback_model="backup-model"), images=["https://img/1.png"])
    assert {b["model"] for b in seen} == {"vl-model"}


@pytest.fixture
def usage_rows():
    token = current_job.set("llmtestjob00001")
    yield lambda: list(SessionLocal().scalars(select(AiUsage).where(AiUsage.job_id == "llmtestjob00001").order_by(AiUsage.id)))
    current_job.reset(token)
    with SessionLocal() as s:
        s.execute(delete(AiUsage).where(AiUsage.job_id == "llmtestjob00001"))
        s.commit()


async def test_usage_is_recorded_per_attempt_under_the_model_that_answered(monkeypatch, usage_rows):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:
            return httpx.Response(500, text="busy")
        # DeepSeek 风格：缓存命中放在 prompt_cache_hit_tokens
        return sse('{"ok": 1}', {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 60,
                                 "completion_tokens_details": {"reasoning_tokens": 5}})

    mock_llm(monkeypatch, handler)
    await llm.chat_json("系统", "用户", settings(llm_fallback_model="backup-model"), purpose="segment")
    rows = usage_rows()
    assert [(r.model, r.status) for r in rows] == [("text-model", "error")] * 3 + [("backup-model", "ok")]
    ok = rows[-1]
    assert (ok.prompt_tokens, ok.completion_tokens, ok.cached_tokens, ok.reasoning_tokens, ok.estimated) == (100, 20, 60, 5, False)
    assert all(r.estimated and r.prompt_tokens > 0 for r in rows[:3])
