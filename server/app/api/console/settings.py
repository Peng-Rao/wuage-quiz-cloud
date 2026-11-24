"""管理后台：大模型、看图模型、向量检索、MinerU 等 API Key 与模型配置（保存后几秒内所有进程生效）。"""

import time
from typing import Any, Literal

import httpx
import httpx2
from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage

from ... import runtime_settings
from ...config import Settings, get_settings
from ...pipeline import llm
from ...console import audit, require
from ...db import ConsoleAccount, SessionLocal
from ...schemas import Model

router = APIRouter(prefix="/api/admin/settings")
can_manage = require("keys")


class SettingsWrite(Model):
    # 键 → 新值；值为 null 时删除后台配置，恢复环境变量的值
    values: dict[str, Any]


class TestRequest(Model):
    target: Literal["llm", "fallback", "vision", "embedding"]


@router.get("")
def read_settings(_: ConsoleAccount = Depends(can_manage)) -> list[dict[str, Any]]:
    return runtime_settings.describe()


@router.put("")
def write_settings(body: SettingsWrite, actor: ConsoleAccount = Depends(can_manage)) -> list[dict[str, Any]]:
    try:
        changed = runtime_settings.save(body.values, actor.email)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    if changed:
        with SessionLocal() as s:
            # 记录修改了哪些项；密钥只记录「已修改 / 已恢复」，不记录值
            detail = {}
            for key in changed:
                spec = runtime_settings.BY_KEY[key]
                value = body.values[key]
                detail[key] = ("恢复环境变量" if value is None else
                               ("已修改" if spec.kind == "secret" else value))
            audit(s, actor, "settings.update", ", ".join(changed), detail)
            s.commit()
    return runtime_settings.describe()


@router.post("/test")
async def test_connection(body: TestRequest, actor: ConsoleAccount = Depends(can_manage)) -> dict[str, Any]:
    """用当前生效的配置发一个最小请求，检查地址、Key 与模型是否可用（只请求一次、不切换备用模型、不计入 AI 用量）。
    大模型与看图模型走与解析相同的 LangChain 调用路径。"""
    runtime_settings.sync(force=True)
    st = get_settings()
    started = time.monotonic()
    if body.target == "embedding":
        model, ok, status, message = await _test_embedding(st)
    else:
        model, ok, status, message = await _test_chat(st, body.target)
    ms = int((time.monotonic() - started) * 1000)
    with SessionLocal() as s:
        audit(s, actor, "settings.test", body.target, {"ok": ok, "status": status})
        s.commit()
    return {"ok": ok, "status": status, "latencyMs": ms, "model": model, "message": message}


async def _test_chat(st: Settings, target: str) -> tuple[str, bool, int | None, str]:
    vision = target == "vision"
    model = st.vision_model if vision else st.llm_fallback_model if target == "fallback" else st.llm_model
    configured = st.vision_enabled if vision else bool(st.llm_base_url and st.llm_api_key)
    if not (model and configured):
        return model, False, None, "地址、Key 或模型未配置"
    try:
        async with httpx2.AsyncClient(transport=llm.transport, timeout=30) as client:
            chat = llm.chat_model(st, vision=vision, model=model, client=client)
            # 要求输出 JSON 时，部分厂商要求消息中出现「JSON」
            await chat.ainvoke([HumanMessage(content='连接测试，请输出 JSON：{"ok": 1}')], max_tokens=16)
        return model, True, 200, "连接正常"
    except Exception as e:  # noqa: BLE001
        err = llm.classify_error(e)
        return model, False, getattr(e, "status_code", None), str(err)


async def _test_embedding(st: Settings) -> tuple[str, bool, int | None, str]:
    if st.embedding_provider != "openai":
        return st.embedding_model, False, None, f"当前向量提供方为 {st.embedding_provider}，仅支持测试 OpenAI 兼容接口"
    model, base, key = st.embedding_model, st.embedding_base_url or st.llm_base_url, st.embedding_api_key or st.llm_api_key
    if not (model and base and key):
        return model, False, None, "地址、Key 或模型未配置"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(base.rstrip("/") + "/embeddings", json={"model": model, "input": "连接测试"},
                                  headers={"Authorization": f"Bearer {key}"})
        ok = r.status_code < 400
        return model, ok, r.status_code, "连接正常" if ok else f"HTTP {r.status_code}：{r.text[:200]}"
    except httpx.HTTPError as e:
        return model, False, None, f"无法连接：{type(e).__name__}"
