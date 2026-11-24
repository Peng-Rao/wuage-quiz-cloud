"""模拟大模型接口。

openai SDK 基于 httpx2，测试中的处理函数仍用 httpx 的 Request / Response 编写：mock_llm 负责转换。
请求要求流式输出（stream=true）而处理函数返回普通的 chat.completion JSON 时，自动改写为 SSE，
因此测试只需关心模型「回答了什么」。
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import httpx2

from app.pipeline import llm

Handler = Callable[[httpx.Request], "httpx.Response | Awaitable[httpx.Response]"]


def completion(content: str, usage: dict[str, Any] | None = None, model: str = "m") -> httpx.Response:
    """非流式的 chat.completion 响应。"""
    body: dict[str, Any] = {"id": "c1", "object": "chat.completion", "created": 0, "model": model,
                            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                         "finish_reason": "stop"}]}
    if usage:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def sse(content: str, usage: dict[str, Any] | None = None, model: str = "m") -> httpx.Response:
    """流式响应：内容分两块发送，最后一块带用量。"""
    def chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
        data = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
        return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"

    half = len(content) // 2
    body = chunk({"role": "assistant", "content": content[:half]}) + chunk({"content": content[half:]}, "stop")
    if usage is not None:
        body += "data: " + json.dumps({"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": model,
                                       "choices": [], "usage": usage}) + "\n\n"
    body += "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _as_sse(resp: httpx.Response) -> httpx.Response:
    try:
        payload = json.loads(resp.content)
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return resp
    return sse(content or "", payload.get("usage"), payload.get("model", "m"))


def mock_llm(monkeypatch, handler: Handler) -> None:  # noqa: ANN001
    """用 httpx 风格的处理函数模拟大模型接口；处理函数抛出的异常原样传给 SDK（模拟网络中断等）。"""

    async def bridge(req2: httpx2.Request) -> httpx2.Response:
        body = await req2.aread()
        req = httpx.Request(req2.method, str(req2.url), headers=list(req2.headers.multi_items()), content=body)
        resp = handler(req)
        if hasattr(resp, "__await__"):
            resp = await resp  # type: ignore[misc]
        await resp.aread()
        wants_stream = bool(body) and json.loads(body).get("stream") is True
        if wants_stream and resp.status_code == 200 and "text/event-stream" not in resp.headers.get("content-type", ""):
            resp = _as_sse(resp)
        return httpx2.Response(resp.status_code, headers=list(resp.headers.multi_items()), content=resp.content)

    t = httpx2.MockTransport(bridge)
    t.httpx_handler = handler  # type: ignore[attr-defined]  # 供其他测试包一层再调用
    monkeypatch.setattr(llm, "transport", t)
