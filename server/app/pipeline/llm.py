"""大模型调用：LangChain ChatOpenAI，接 OpenAI 兼容的 /chat/completions（DeepSeek、通义千问、腾讯云 TokenHub 等）。

- chat_model()：按配置创建文字 / 看图模型。默认流式输出（生成长 JSON 耗时较长，非流式请求在网关侧容易因长时间无数据被断开）。
- 失败分类：Key 无效直接报错；额度用完、请求被拒（如模型名错误）不重试；网络中断、超时、5xx、限流按指数退避重试。
  配置了 LLM_FALLBACK_MODEL 时，重试后仍失败（或额度用完、请求被拒）改用备用模型。
- structured()：提示词 → 模型 → 宽松解析 JSON → Pydantic 校验 →（可选）调用方的语义检查；不合格时把错误发给模型重答一次，
  仍不合格才抛出 LLMError，由调用方降级（规则拆题、不标注等）。
- 每次请求（含重试、重答、备用模型）都由回调写入 ai_usage，失败的请求按字符数估算用量。
"""

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

import httpx2
import openai
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import LLMResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from .. import usage as usage_log
from ..config import Settings

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# 测试时可替换为 httpx2.MockTransport（openai SDK 基于 httpx2），见 tests/llm_fakes.py
transport: httpx2.AsyncBaseTransport | None = None
# 重试等待（秒）：initial·2^n，不超过 max，另加 0–jitter 的随机抖动；测试中设为 0
RETRY_WAIT: dict[str, float] = {"initial": 2.0, "max": 30.0, "jitter": 1.0}
# 默认每次调用最多请求 1 + RETRIES 次（不含格式修正的重答）
RETRIES = 2

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """大模型不可用或输出不可用，调用方据此降级。"""


class FatalLLMError(LLMError):
    """Key 无效：重试和换模型都无用。"""


class RejectedError(LLMError):
    """额度用完、模型不存在等被服务端拒绝的请求：不重试，可换备用模型。"""


class TransientLLMError(LLMError):
    """网络中断、超时、5xx、限流等临时错误：退避重试，仍失败时换备用模型。"""


def _parse_json(text: str) -> Any:
    text = _FENCE.sub("", text.strip())
    if not text:
        raise ValueError("回答为空")
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
    """连接类异常的 str() 常为空，补上异常类型便于排查。"""
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
        """OpenAI 格式的原始用量（usage 字段）。"""
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

    @classmethod
    def from_metadata(cls, m: dict[str, Any] | None) -> "Usage | None":
        """LangChain 的 usage_metadata。"""
        if not m:
            return None
        return cls(
            prompt=int(m.get("input_tokens") or 0),
            completion=int(m.get("output_tokens") or 0),
            reasoning=int((m.get("output_token_details") or {}).get("reasoning") or 0),
            cached=int((m.get("input_token_details") or {}).get("cache_read") or 0),
        )


# ---------------- 失败分类 ----------------

_QUOTA_WORDS = ("quota", "free tier", "freetier", "exhausted", "insufficient", "arrearage", "billing", "额度", "余额", "欠费")


def classify_error(e: BaseException) -> LLMError:
    """把 SDK / 网络异常归为 FatalLLMError、RejectedError 或 TransientLLMError。"""
    if isinstance(e, LLMError):
        return e
    if isinstance(e, openai.APIStatusError):
        status = e.status_code
        body = e.body if isinstance(e.body, str) else json.dumps(e.body, ensure_ascii=False) if e.body else ""
        text = f"{e.message} {body}".strip()
        low = text.lower()
        if status == 401 or (status == 403 and ("apikey" in low.replace(" ", "").replace("-", "") or "api key" in low)):
            return FatalLLMError("大模型 API Key 无效")
        if status == 402 or (status in (403, 429) and any(w in low for w in _QUOTA_WORDS)):
            return RejectedError(f"大模型额度已用完（HTTP {status}）：{text[:200]}")
        if status in (400, 404, 422):
            return RejectedError(f"大模型拒绝了请求（HTTP {status}）：{text[:200]}")
        return TransientLLMError(f"大模型调用失败（HTTP {status}）：{text[:200]}")
    if isinstance(e, openai.APIConnectionError):
        # SDK 只说「Connection error.」，取原始异常（如 RemoteProtocolError、ssl.SSLError）便于排查
        return TransientLLMError(f"大模型调用失败：{describe(_root(e))}")
    if isinstance(e, openai.APIError):  # 流式响应中途返回的 error 事件
        return TransientLLMError(f"大模型返回错误：{describe(e)}")
    # 其余（含流式读取中途抛出的 ssl.SSLError、超时等）都按临时错误重试
    return TransientLLMError(f"大模型调用失败：{describe(_root(e))}")


def _root(e: BaseException) -> BaseException:
    seen = set()
    while e.__cause__ is not None and id(e) not in seen:
        seen.add(id(e))
        e = e.__cause__
    return e


# ---------------- 模型 ----------------

class _ChatOpenAI(ChatOpenAI):
    """DeepSeek 把缓存命中的输入 token 放在 usage.prompt_cache_hit_tokens，转成 OpenAI 的字段后再交给 LangChain 统计。"""

    def _convert_chunk_to_generation_chunk(self, chunk: dict, default_chunk_class: type,
                                           base_generation_info: dict | None):  # noqa: ANN202
        usage = chunk.get("usage")
        if usage and usage.get("prompt_cache_hit_tokens") and not (usage.get("prompt_tokens_details") or {}).get("cached_tokens"):
            details = {**(usage.get("prompt_tokens_details") or {}), "cached_tokens": usage["prompt_cache_hit_tokens"]}
            chunk = {**chunk, "usage": {**usage, "prompt_tokens_details": details}}
        return super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)


def chat_model(settings: Settings, *, vision: bool = False, model: str | None = None,
               client: httpx2.AsyncClient | None = None) -> ChatOpenAI:
    """文字模型（LLM_*，要求输出 JSON 对象）或看图模型（VISION_*，部分看图模型不支持 response_format）。"""
    if vision:
        base_url = settings.vision_base_url or settings.llm_base_url
        api_key = settings.vision_api_key or settings.llm_api_key
        extra = dict(settings.llm_extra_body if settings.vision_extra_body is None else settings.vision_extra_body)
        model = model or settings.vision_model
    else:
        base_url, api_key, extra = settings.llm_base_url, settings.llm_api_key, dict(settings.llm_extra_body)
        model = model or settings.llm_model
        # 放在 extra_body 而不是 model_kwargs：后者会让 LangChain 改走 SDK 的 beta 解析流，对非 OpenAI 厂商的数据块校验过严
        extra = {"response_format": {"type": "json_object"}, **extra}
    return _ChatOpenAI(
        model=model, base_url=base_url, api_key=api_key, temperature=0, timeout=settings.llm_timeout,
        max_retries=0, streaming=True, stream_usage=True, extra_body=extra or None, http_async_client=client,
    )


def _resilient(settings: Settings, *, vision: bool, client: httpx2.AsyncClient, attempts: int) -> Runnable:
    """模型调用 + 失败分类 + 指数退避重试，配置了备用模型时再接上备用模型（只用于文字模型）。"""

    def one(model: str) -> Runnable:
        chat = chat_model(settings, vision=vision, model=model, client=client)

        async def call(messages: list[BaseMessage], config: RunnableConfig) -> BaseMessage:
            try:
                return await chat.ainvoke(messages, config=config)
            except Exception as e:  # noqa: BLE001
                raise classify_error(e) from e

        return RunnableLambda(call, name=model).with_retry(
            retry_if_exception_type=(TransientLLMError,), wait_exponential_jitter=True,
            exponential_jitter_params={"initial": RETRY_WAIT["initial"], "max": RETRY_WAIT["max"],
                                       "jitter": RETRY_WAIT["jitter"]},
            stop_after_attempt=max(1, attempts),
        )

    primary = one(settings.vision_model if vision else settings.llm_model)
    fallback = settings.llm_fallback_model.strip()
    if vision or not fallback or fallback == settings.llm_model:
        return primary
    return primary.with_fallbacks([one(fallback)], exceptions_to_handle=(TransientLLMError, RejectedError))


# ---------------- 用量 ----------------

def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content or [])


class UsageCallback(BaseCallbackHandler):
    """把每次模型请求（成功或失败）写入 ai_usage；与调用在同一上下文中执行，以便取到当前解析任务。"""

    run_inline = True

    def __init__(self, purpose: str):
        self.purpose = purpose
        self.runs: dict[UUID, tuple[str, str, float]] = {}

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID,
                            **kwargs: Any) -> None:
        params = kwargs.get("invocation_params") or {}
        model = params.get("model") or params.get("model_name") or "unknown"
        prompt = "".join(_text(m.content) for batch in messages for m in batch)
        self.runs[run_id] = (model, prompt, time.monotonic())

    def _record(self, run_id: UUID, content: str, used: Usage | None, status: str) -> None:
        model, prompt, started = self.runs.pop(run_id, ("unknown", "", time.monotonic()))
        estimated = used is None
        if used is None:
            # 服务端未返回用量（或请求中途失败）：按字符数估算；失败请求多数厂商仍按输入计费
            used = Usage(prompt=usage_log.estimate_tokens(prompt), completion=usage_log.estimate_tokens(content))
        usage_log.record(
            "llm", self.purpose, model, prompt_tokens=used.prompt, completion_tokens=used.completion,
            reasoning_tokens=used.reasoning, cached_tokens=used.cached,
            duration_ms=int((time.monotonic() - started) * 1000), estimated=estimated, status=status,
        )

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        gen = response.generations[0][0] if response.generations and response.generations[0] else None
        message = getattr(gen, "message", None)
        used = Usage.from_metadata(getattr(message, "usage_metadata", None))
        self._record(run_id, gen.text if gen else "", used, "ok")

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._record(run_id, "", None, "error")


# ---------------- 调用 ----------------

async def complete(messages: list[BaseMessage], settings: Settings, *, purpose: str, vision: bool = False,
                   retries: int = RETRIES) -> str:
    """发送消息，返回模型输出的文字（含重试与备用模型）。"""
    if vision and not settings.vision_enabled:
        raise LLMError("未配置看图模型")
    if not vision and not settings.llm_enabled:
        raise LLMError("未配置大模型")
    async with httpx2.AsyncClient(transport=transport, timeout=settings.llm_timeout) as client:
        runnable = _resilient(settings, vision=vision, client=client, attempts=retries + 1)
        try:
            message = await runnable.ainvoke(messages, config={"callbacks": [UsageCallback(purpose)]})
        except LLMError as e:
            log.warning("大模型调用失败（%s）：%s", purpose, e)
            raise
    return _text(message.content)


REPAIR = "你上一次的回答不符合要求：{error}\n请修正后重新输出完整的 JSON，只输出 JSON，不要任何说明文字。"


def _brief(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "；".join(f"{'.'.join(str(x) for x in err['loc']) or '整体'}：{err['msg']}" for err in e.errors()[:3])
    return str(e)[:300] or type(e).__name__


def _messages(prompt: ChatPromptTemplate | list[BaseMessage], values: dict[str, Any] | None,
              images: list[str] | None) -> list[BaseMessage]:
    messages = prompt.format_messages(**(values or {})) if isinstance(prompt, ChatPromptTemplate) else list(prompt)
    if images:
        # 图片按题目顺序附在最后一条用户消息的文字之后
        last = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        parts: list[Any] = [{"type": "text", "text": _text(messages[last].content)}]
        parts += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        messages[last] = HumanMessage(content=parts)
    return messages


def prompt(system: str, *, template: bool = True, human: str = "{input}") -> ChatPromptTemplate:
    """系统提示 + 用户输入的提示词模板。template=False 时系统提示原样发送（其中的 JSON 示例不必把花括号写成 {{ }}）。"""
    head: Any = ("system", system) if template else SystemMessage(content=system)
    return ChatPromptTemplate.from_messages([head, ("human", human)])


async def structured(prompt: ChatPromptTemplate | list[BaseMessage], values: dict[str, Any] | None,
                     schema: type[T] | None, settings: Settings, *, purpose: str, images: list[str] | None = None,
                     check: Callable[[Any], Any] | None = None, retries: int = RETRIES) -> Any:
    """按提示词调用模型并返回校验后的结果：schema 为空时返回解析出的 JSON。
    check 接收校验后的结果，可做与输入相关的检查并返回整理后的结果（返回 None 时沿用原结果）。
    JSON 无效、不符合 schema 或 check 抛出 ValueError 时，把错误告诉模型重答一次；仍不合格抛出 LLMError。
    images 为图片地址（http(s) 或 data:image/...;base64,...），非空时改用看图模型。"""
    messages = _messages(prompt, values, images)
    for attempt in range(2):
        text = await complete(messages, settings, purpose=purpose, vision=bool(images), retries=retries)
        try:
            data = _parse_json(text)
            result = schema.model_validate(data) if schema is not None else data
            if check is not None:
                checked = check(result)
                result = result if checked is None else checked
            return result
        except ValueError as e:  # 含 json.JSONDecodeError、pydantic.ValidationError
            if attempt:
                raise LLMError(f"大模型输出不符合要求：{_brief(e)}") from e
            log.warning("大模型输出不符合要求（%s），请求修正：%s", purpose, _brief(e))
            messages = [*messages, AIMessage(content=text or "（空）"), HumanMessage(content=REPAIR.format(error=_brief(e)))]
    raise AssertionError("unreachable")


async def chat_json(system: str, user: str, settings: Settings, *, purpose: str = "other", retries: int = RETRIES,
                    images: list[str] | None = None) -> Any:
    """不做 schema 校验的简便调用：返回模型输出的 JSON。"""
    return await structured([SystemMessage(content=system), HumanMessage(content=user)], None, None, settings,
                            purpose=purpose, images=images, retries=retries)
