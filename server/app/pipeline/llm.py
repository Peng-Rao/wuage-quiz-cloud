"""OpenAI 兼容的 /chat/completions 客户端（DeepSeek、通义千问、Kimi 等均支持）。

直接走 HTTP 协议而非 SDK：协议稳定，便于切换厂商，也便于测试时模拟。
默认使用流式输出：大模型生成长 JSON 耗时较长，非流式请求在网关侧容易因长时间无数据被断开。
"""

import json
import logging
import re
from typing import Any

import httpx

from ..config import Settings

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# 测试时可替换为 httpx.MockTransport
transport: httpx.AsyncBaseTransport | None = None


class LLMError(Exception):
    pass


def _parse_json(text: str) -> Any:
    text = _FENCE.sub("", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 部分模型会在 JSON 前后附加说明文字
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def describe(e: BaseException) -> str:
    """httpx 的连接类异常 str() 常为空，补上异常类型便于排查。"""
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


async def _read_stream(r: httpx.Response) -> str:
    """拼接 SSE 中的 delta.content；思考模型的 reasoning_content 不计入结果。"""
    parts: list[str] = []
    async for line in r.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        if err := chunk.get("error"):
            raise LLMError(f"大模型返回错误：{err.get('message', err) if isinstance(err, dict) else err}")
        for choice in chunk.get("choices") or []:
            parts.append((choice.get("delta") or {}).get("content") or "")
    return "".join(parts)


async def chat_json(system: str, user: str, settings: Settings, *, retries: int = 1) -> Any:
    if not settings.llm_enabled:
        raise LLMError("未配置大模型")
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "stream": True,
        **settings.llm_extra_body,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    last: Exception | None = None
    async with httpx.AsyncClient(timeout=settings.llm_timeout, transport=transport) as client:
        for attempt in range(retries + 1):
            try:
                async with client.stream("POST", url, json=body, headers=headers) as r:
                    if r.status_code in (401, 403):
                        raise LLMError("大模型 API Key 无效")
                    if r.status_code >= 400:
                        await r.aread()
                        raise httpx.HTTPStatusError(f"HTTP {r.status_code}：{r.text[:200]}", request=r.request, response=r)
                    if r.headers.get("content-type", "").startswith("text/event-stream"):
                        content = await _read_stream(r)
                    else:  # 不支持流式的服务直接返回完整 JSON
                        await r.aread()
                        content = r.json()["choices"][0]["message"]["content"]
                if not content.strip():
                    raise LLMError("大模型返回内容为空")
                return _parse_json(content)
            except LLMError as e:
                if "Key" in str(e):
                    raise
                last = e
            except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as e:
                last = e
            log.warning("大模型调用失败（第 %d 次）：%s", attempt + 1, describe(last))
    raise LLMError(f"大模型调用失败：{describe(last)}")  # type: ignore[arg-type]
