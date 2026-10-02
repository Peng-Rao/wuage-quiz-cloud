"""OpenAI 兼容的 /chat/completions 客户端（DeepSeek、通义千问、Kimi 等均支持）。

直接走 HTTP 协议而非 SDK：协议稳定，便于切换厂商，也便于测试时模拟。
默认使用流式输出：大模型生成长 JSON 耗时较长，非流式请求在网关侧容易因长时间无数据被断开。
"""

import json
import logging
import re
import ssl
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .. import usage as usage_log
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


def items_of(data: Any, key: str) -> list[Any] | None:
    """取模型输出中的列表：要求的格式是 {key: [...]}，但部分模型（如 qwen3.8）会省略外层对象、直接返回数组，两种都接受。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    return None


def describe(e: BaseException) -> str:
    """httpx 的连接类异常 str() 常为空，补上异常类型便于排查。"""
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0
    reasoning: int = 0
    cached: int = 0

    @classmethod
    def parse(cls, u: dict[str, Any] | None) -> "Usage | None":
        if not u:
            return None
        details = u.get("completion_tokens_details") or {}
        prompt_details = u.get("prompt_tokens_details") or {}
        return cls(
            prompt=int(u.get("prompt_tokens") or 0),
            completion=int(u.get("completion_tokens") or 0),
            reasoning=int(details.get("reasoning_tokens") or 0),
            # OpenAI / 通义：prompt_tokens_details.cached_tokens；DeepSeek：prompt_cache_hit_tokens
            cached=int(prompt_details.get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0),
        )


async def _read_stream(r: httpx.Response) -> tuple[str, Usage | None]:
    """拼接 SSE 中的 delta.content；思考模型的 reasoning_content 不计入结果。用量在最后一个数据块中返回。"""
    parts: list[str] = []
    usage: Usage | None = None
    async for line in r.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        if err := chunk.get("error"):
            raise LLMError(f"大模型返回错误：{err.get('message', err) if isinstance(err, dict) else err}")
        usage = Usage.parse(chunk.get("usage")) or usage
        for choice in chunk.get("choices") or []:
            parts.append((choice.get("delta") or {}).get("content") or "")
    return "".join(parts), usage


def _log_usage(model: str, purpose: str, prompt_text: str, content: str, u: Usage | None,
               started: float, status: str) -> None:
    estimated = u is None
    if u is None:
        # 服务端未返回用量（或请求中途失败）：按字符数估算；失败请求多数厂商仍按输入计费
        u = Usage(prompt=usage_log.estimate_tokens(prompt_text), completion=usage_log.estimate_tokens(content))
    usage_log.record(
        "llm", purpose, model, prompt_tokens=u.prompt, completion_tokens=u.completion,
        reasoning_tokens=u.reasoning, cached_tokens=u.cached, duration_ms=int((time.monotonic() - started) * 1000),
        estimated=estimated, status=status,
    )


_QUOTA_WORDS = ("quota", "free tier", "freetier", "exhausted", "insufficient", "arrearage", "billing", "额度", "余额", "欠费")


def _http_error(status: int, text: str) -> Exception:
    """HTTP 错误 → 异常：Key 无效与额度用完直接报错（重试无用），其他错误可重试。"""
    low = text.lower()
    if status == 401 or (status == 403 and ("apikey" in low.replace(" ", "") or "api key" in low)):
        return LLMError("大模型 API Key 无效")
    if status == 402 or (status in (403, 429) and any(w in low for w in _QUOTA_WORDS)):
        return LLMError(f"大模型额度已用完（HTTP {status}）：{text[:200]}")
    return httpx.HTTPStatusError(f"HTTP {status}：{text[:200]}", request=None, response=None)  # type: ignore[arg-type]


async def chat_json(system: str, user: str, settings: Settings, *, purpose: str = "other", retries: int = 1,
                    images: list[str] | None = None) -> Any:
    """images 为图片地址（http(s) 或 data:image/...;base64,...）；非空时改用看图模型（VISION_MODEL），按题目顺序附在文字之后。"""
    if images:
        if not settings.vision_enabled:
            raise LLMError("未配置看图模型")
        model = settings.vision_model
        base_url = settings.vision_base_url or settings.llm_base_url
        api_key = settings.vision_api_key or settings.llm_api_key
        extra = settings.llm_extra_body if settings.vision_extra_body is None else settings.vision_extra_body
        content: Any = [{"type": "text", "text": user}] + [{"type": "image_url", "image_url": {"url": u}} for u in images]
        # 部分看图模型不支持 response_format，依靠提示词约束输出 JSON，解析时容忍前后说明文字
        fmt: dict[str, Any] = {}
    else:
        if not settings.llm_enabled:
            raise LLMError("未配置大模型")
        model, base_url, api_key, extra = settings.llm_model, settings.llm_base_url, settings.llm_api_key, \
            settings.llm_extra_body
        content = user
        fmt = {"response_format": {"type": "json_object"}}
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "temperature": 0,
        **fmt,
        "stream": True,
        "stream_options": {"include_usage": True},
        **extra,
    }
    return await _call(model, base_url, api_key, body, system + user, settings, purpose, retries)


async def _call(model: str, base_url: str, api_key: str, body: dict[str, Any], prompt_text: str, settings: Settings,
                purpose: str, retries: int) -> Any:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    last: Exception | None = None
    async with httpx.AsyncClient(timeout=settings.llm_timeout, transport=transport) as client:
        for attempt in range(retries + 1):
            started = time.monotonic()
            content, used = "", None
            try:
                async with client.stream("POST", url, json=body, headers=headers) as r:
                    if r.status_code >= 400:
                        await r.aread()
                        err = _http_error(r.status_code, r.text)
                        if isinstance(err, httpx.HTTPStatusError):
                            err = httpx.HTTPStatusError(str(err), request=r.request, response=r)
                        raise err
                    if r.headers.get("content-type", "").startswith("text/event-stream"):
                        content, used = await _read_stream(r)
                    else:  # 不支持流式的服务直接返回完整 JSON
                        await r.aread()
                        payload = r.json()
                        content = payload["choices"][0]["message"]["content"]
                        used = Usage.parse(payload.get("usage"))
                if not content.strip():
                    raise LLMError("大模型返回内容为空")
                result = _parse_json(content)
                _log_usage(model, purpose, prompt_text, content, used, started, "ok")
                return result
            except LLMError as e:
                if "Key 无效" in str(e) or "额度已用完" in str(e):
                    raise
                last = e
            # 跨境链路偶发 TLS 中断时，流式读取可能直接抛出未被 httpx 包装的 ssl.SSLError，同样重试
            except (httpx.HTTPError, ssl.SSLError, KeyError, IndexError, json.JSONDecodeError) as e:
                last = e
            # 请求已发出即可能计费，失败也记一笔
            _log_usage(model, purpose, prompt_text, content, used, started, "error")
            log.warning("大模型 %s 调用失败（第 %d 次）：%s", model, attempt + 1, describe(last))
    raise LLMError(f"大模型调用失败：{describe(last)}")  # type: ignore[arg-type]
