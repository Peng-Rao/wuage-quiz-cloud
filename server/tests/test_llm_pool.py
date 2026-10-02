"""大模型模型池：额度用完 / 限流 / 模型不可用 / 超出预算时换下一个模型，看图请求只用看图模型池。"""

import json

import httpx
import pytest

from app.config import Settings
from app.pipeline import llm


@pytest.fixture(autouse=True)
def _reset():
    llm._cooldown.clear()
    llm._spent_cache.clear()
    yield
    llm._cooldown.clear()


def settings(**kw) -> Settings:  # noqa: ANN003
    base = dict(llm_base_url="https://llm.test/v1", llm_api_key="k", llm_extra_body={"enable_thinking": False},
                llm_models=["a", "b", "c"], vision_models=["va", "vb"])
    return Settings(**{**base, **kw})


def mock(monkeypatch, responses: dict[str, list[tuple[int, str]]]) -> list[dict]:
    """按模型依次返回 (状态码, 内容)；成功时内容为模型输出。记录每次请求体。"""
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        status, text = responses[body["model"]].pop(0)
        if status != 200:
            return httpx.Response(status, text=text)
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handler))
    return seen


QUOTA = '{"error":{"code":"AllocationQuota.FreeTierOnly","message":"The free tier of the model has been exhausted."}}'


async def test_switches_on_quota_and_remembers(monkeypatch):
    seen = mock(monkeypatch, {"a": [(403, QUOTA)], "b": [(200, '{"x": 1}'), (200, '{"x": 2}')]})
    s = settings()
    assert await llm.chat_json("sys", "u", s) == {"x": 1}
    # 额度用完的模型在冷却期内直接跳过
    assert await llm.chat_json("sys", "u", s) == {"x": 2}
    assert [b["model"] for b in seen] == ["a", "b", "b"]
    assert llm.available_models(s.text_models, s) == (["b", "c"], {"a": "额度已用完"})


@pytest.mark.parametrize("status,text", [
    (429, "Requests rate limit exceeded"),
    (400, '{"error":{"code":"invalid_parameter_error","message":"The value of the enable_thinking parameter is restricted to True."}}'),
    (404, "model_not_found"),
])
async def test_switches_on_rate_limit_and_unavailable_model(monkeypatch, status, text):
    seen = mock(monkeypatch, {"a": [(status, text)], "b": [(200, '{"ok": true}')]})
    assert await llm.chat_json("sys", "u", settings()) == {"ok": True}
    assert [b["model"] for b in seen] == ["a", "b"]


async def test_invalid_key_and_generic_errors_do_not_switch(monkeypatch):
    seen = mock(monkeypatch, {"a": [(401, "Invalid API-key provided.")]})
    with pytest.raises(llm.LLMError, match="Key 无效"):
        await llm.chat_json("sys", "u", settings())
    assert [b["model"] for b in seen] == ["a"]
    # 普通错误在同一模型上重试，不消耗其他模型的额度
    seen = mock(monkeypatch, {"a": [(500, "boom"), (500, "boom")]})
    with pytest.raises(llm.LLMError, match="调用失败"):
        await llm.chat_json("sys", "u", settings(), retries=1)
    assert [b["model"] for b in seen] == ["a", "a"]


async def test_per_model_extra_body(monkeypatch):
    seen = mock(monkeypatch, {"a": [(200, "{}")], "b": [(200, "{}")]})
    s = settings(llm_models=["a", "b"], llm_model_extra_body={"b": {"enable_thinking": True, "top_p": None}})
    await llm.chat_json("sys", "u", s)
    llm._cooldown["a"] = (1e18, "测试")
    await llm.chat_json("sys", "u", s)
    assert seen[0]["enable_thinking"] is False and seen[1]["enable_thinking"] is True and "top_p" not in seen[1]


async def test_budget_skips_model(monkeypatch):
    seen = mock(monkeypatch, {"b": [(200, "{}")]})
    monkeypatch.setattr(llm, "_spent", lambda m, s: {"a": 1000}.get(m, 0))
    s = settings(llm_model_budget={"a": 1000, "b": 5000})
    await llm.chat_json("sys", "u", s)
    assert [b["model"] for b in seen] == ["b"]


async def test_vision_requests_use_vision_pool(monkeypatch):
    seen = mock(monkeypatch, {"va": [(429, "rate limit")], "vb": [(200, '{"answer": "B"}')]})
    assert await llm.chat_json("sys", "题目", settings(), images=["https://img/1.png"]) == {"answer": "B"}
    assert [b["model"] for b in seen] == ["va", "vb"]
    assert seen[1]["messages"][1]["content"][1] == {"type": "image_url", "image_url": {"url": "https://img/1.png"}}


async def test_all_models_unavailable(monkeypatch):
    mock(monkeypatch, {m: [(403, QUOTA)] for m in "abc"})
    with pytest.raises(llm.LLMError, match="模型池中没有可用的模型：a 额度已用完；b 额度已用完；c 额度已用完"):
        await llm.chat_json("sys", "u", settings())
    # 单个模型（未配置模型池）时提示具体原因
    mock(monkeypatch, {"solo": [(403, QUOTA)]})
    with pytest.raises(llm.LLMError, match="大模型 solo 额度已用完"):
        await llm.chat_json("sys", "u", settings(llm_models=[], llm_model="solo"))


def test_legacy_single_model_settings():
    s = Settings(llm_base_url="u", llm_api_key="k", llm_model="m", vision_model="v")
    assert s.text_models == ["m"] and s.vision_model_pool == ["v"] and s.llm_enabled and s.vision_enabled
