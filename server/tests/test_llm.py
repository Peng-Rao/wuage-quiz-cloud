"""大模型客户端：看图请求用 VISION_MODEL、Key 无效与额度用完直接报错、TLS 中断重试。"""

import json
import ssl

import httpx
import pytest

from app.config import Settings
from app.pipeline import llm


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

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handler))
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

    class Flaky(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                class Broken(httpx.AsyncByteStream):
                    async def __aiter__(self):
                        yield b'data: {"choices":[{"delta":{"content":"{"}}]}\n\n'
                        raise ssl.SSLError("[SSL: RECORD_LAYER_FAILURE] record layer failure")
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Broken())
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}]})

    monkeypatch.setattr(llm, "transport", Flaky())
    assert await llm.chat_json("sys", "u", settings(), retries=1) == {"ok": 1}
    assert calls["n"] == 2
