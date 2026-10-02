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


# ---------------- 模型池 ----------------

# 模型暂停使用到的时间（time.monotonic）与原因；进程内共享
_cooldown: dict[str, tuple[float, str]] = {}
# 预算用量缓存：模型 → (查询时间, 已用 token)
_spent_cache: dict[str, tuple[float, int]] = {}
RATE_LIMIT_COOLDOWN = 60
UNAVAILABLE_COOLDOWN = 600
_QUOTA_WORDS = ("quota", "free tier", "freetier", "exhausted", "insufficient", "arrearage", "billing", "额度", "余额", "欠费")
_UNAVAILABLE_WORDS = ("model_not_found", "model not found", "does not exist", "not exist", "not support", "unsupported",
                      "restricted", "invalid_parameter", "access denied", "no permission", "not activated")


class _Switch(Exception):
    """当前模型暂不可用（额度、限流、不支持请求），换池中的下一个模型。"""

    def __init__(self, reason: str, cooldown: float):
        super().__init__(reason)
        self.reason, self.cooldown = reason, cooldown


def _classify_http(status: int, text: str, settings: Settings) -> Exception:
    low = text.lower()
    if status == 401 or (status == 403 and ("apikey" in low.replace(" ", "") or "api key" in low)):
        return LLMError("大模型 API Key 无效")
    if status == 402 or (status in (403, 429) and any(w in low for w in _QUOTA_WORDS)):
        return _Switch("额度已用完", settings.llm_exhausted_cooldown)
    if status == 429:
        return _Switch("请求过于频繁", RATE_LIMIT_COOLDOWN)
    if status in (403, 404) or (status == 400 and any(w in low for w in _UNAVAILABLE_WORDS)):
        return _Switch(f"模型不可用（HTTP {status}）", UNAVAILABLE_COOLDOWN)
    return httpx.HTTPStatusError(f"HTTP {status}：{text[:200]}", request=None, response=None)  # type: ignore[arg-type]


def _spent(model: str, settings: Settings) -> int:
    """本服务记录的该模型 token 用量（自 LLM_BUDGET_SINCE 起），缓存 30 秒。"""
    now = time.monotonic()
    hit = _spent_cache.get(model)
    if hit and now - hit[0] < 30:
        return hit[1]
    from sqlalchemy import func, select

    from ..db import AiUsage, SessionLocal

    q = select(func.coalesce(func.sum(AiUsage.prompt_tokens + AiUsage.completion_tokens), 0)).where(
        AiUsage.provider == "llm", AiUsage.model == model)
    if settings.llm_budget_since:
        q = q.where(AiUsage.created_at >= settings.llm_budget_since)
    try:
        with SessionLocal() as s:
            spent = int(s.scalar(q) or 0)
    except Exception:  # noqa: BLE001 — 统计失败不影响调用
        spent = 0
    _spent_cache[model] = (now, spent)
    return spent


def available_models(pool: list[str], settings: Settings) -> tuple[list[str], dict[str, str]]:
    """池中当前可用的模型（保持顺序），以及不可用模型的原因。"""
    now = time.monotonic()
    ok, skipped = [], {}
    for m in pool:
        until = _cooldown.get(m)
        if until and until[0] > now:
            skipped[m] = until[1]
            continue
        budget = settings.llm_model_budget.get(m)
        if budget is not None and _spent(m, settings) >= budget:
            skipped[m] = "已达到用量预算"
            continue
        ok.append(m)
    return ok, skipped


def _extra_for(model: str, base: dict[str, Any], settings: Settings) -> dict[str, Any]:
    merged = {**base, **settings.llm_model_extra_body.get(model, {})}
    return {k: v for k, v in merged.items() if v is not None}


def _apply_model_overrides(body: dict[str, Any], model: str, settings: Settings) -> dict[str, Any]:
    """按模型覆盖请求参数：值为 null 的键从请求中删除（包括 response_format 等内置参数），
    如思考模式下不支持 JSON 输出模式的模型：{"glm-5.3": {"enable_thinking": true, "response_format": null}}。"""
    for k, v in settings.llm_model_extra_body.get(model, {}).items():
        if v is None:
            body.pop(k, None)
        else:
            body[k] = v
    return body


async def chat_json(system: str, user: str, settings: Settings, *, purpose: str = "other", retries: int = 1,
                    images: list[str] | None = None) -> Any:
    """images 为图片地址（http(s) 或 data:image/...;base64,...）；非空时改用看图模型池，按题目顺序附在文字之后。
    模型池按顺序使用：额度用完、限流、模型不可用或超出预算时换下一个；其他错误在同一模型上重试。"""
    if images:
        if not settings.vision_enabled:
            raise LLMError("未配置看图模型")
        pool = settings.vision_model_pool
        base_url = settings.vision_base_url or settings.llm_base_url
        api_key = settings.vision_api_key or settings.llm_api_key
        base_extra = settings.llm_extra_body if settings.vision_extra_body is None else settings.vision_extra_body
        content: Any = [{"type": "text", "text": user}] + [{"type": "image_url", "image_url": {"url": u}} for u in images]
        # 部分看图模型不支持 response_format，依靠提示词约束输出 JSON，解析时容忍前后说明文字
        fmt: dict[str, Any] = {}
    else:
        if not settings.llm_enabled:
            raise LLMError("未配置大模型")
        pool, base_url, api_key, base_extra = settings.text_models, settings.llm_base_url, settings.llm_api_key, \
            settings.llm_extra_body
        content = user
        fmt = {"response_format": {"type": "json_object"}}

    models, skipped = available_models(pool, settings)
    for model in models:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": 0,
            **fmt,
            "stream": True,
            "stream_options": {"include_usage": True},
            **base_extra,
        }
        _apply_model_overrides(body, model, settings)
        try:
            return await _call(model, base_url, api_key, body, system + user, settings, purpose, retries)
        except _Switch as e:
            _cooldown[model] = (time.monotonic() + e.cooldown, e.reason)
            skipped[model] = e.reason
            log.warning("模型 %s %s，换下一个模型", model, e.reason)
    if len(pool) == 1 and skipped:
        raise LLMError(f"大模型 {pool[0]} {skipped[pool[0]]}")
    raise LLMError("模型池中没有可用的模型：" + "；".join(f"{m} {r}" for m, r in skipped.items()))


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
                        err = _classify_http(r.status_code, r.text, settings)
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
            except _Switch:
                raise
            except LLMError as e:
                if "Key" in str(e):
                    raise
                last = e
            # 跨境链路偶发 TLS 中断时，流式读取可能直接抛出未被 httpx 包装的 ssl.SSLError，同样重试
            except (httpx.HTTPError, ssl.SSLError, KeyError, IndexError, json.JSONDecodeError) as e:
                last = e
            # 请求已发出即可能计费，失败也记一笔
            _log_usage(model, purpose, prompt_text, content, used, started, "error")
            log.warning("大模型 %s 调用失败（第 %d 次）：%s", model, attempt + 1, describe(last))
    raise LLMError(f"大模型调用失败：{describe(last)}")  # type: ignore[arg-type]
