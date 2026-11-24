"""管理后台：大模型、看图模型、向量检索、MinerU 等 API Key 与模型配置（保存后几秒内所有进程生效）。"""

import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ... import runtime_settings
from ...config import get_settings
from ...console import audit, require
from ...db import ConsoleAccount, SessionLocal
from ...schemas import Model

router = APIRouter(prefix="/api/admin/settings")
can_manage = require("keys")


class SettingsWrite(Model):
    # 键 → 新值；值为 null 时删除后台配置，恢复环境变量的值
    values: dict[str, Any]


class TestRequest(Model):
    target: Literal["llm", "vision", "embedding"]


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
    """用当前生效的配置发一个最小请求，检查地址、Key 与模型是否可用（不计入 AI 用量）。"""
    runtime_settings.sync(force=True)
    st = get_settings()
    if body.target == "embedding":
        if st.embedding_provider != "openai":
            return {"ok": False, "message": f"当前向量提供方为 {st.embedding_provider}，仅支持测试 OpenAI 兼容接口"}
        model, base, key = st.embedding_model, st.embedding_base_url or st.llm_base_url, st.embedding_api_key or st.llm_api_key
        path, payload = "/embeddings", {"model": model, "input": "连接测试"}
    else:
        if body.target == "vision":
            model, base, key = st.vision_model, st.vision_base_url or st.llm_base_url, st.vision_api_key or st.llm_api_key
            extra = st.llm_extra_body if st.vision_extra_body is None else st.vision_extra_body
        else:
            model, base, key, extra = st.llm_model, st.llm_base_url, st.llm_api_key, st.llm_extra_body
        path = "/chat/completions"
        payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1,
                   "stream": False, **extra}
    if not (model and base and key):
        return {"ok": False, "message": "地址、Key 或模型未配置"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(base.rstrip("/") + path, json=payload, headers={"Authorization": f"Bearer {key}"})
        ok, status = r.status_code < 400, r.status_code
        message = "连接正常" if ok else f"HTTP {r.status_code}：{r.text[:200]}"
    except httpx.HTTPError as e:
        ok, status, message = False, None, f"无法连接：{type(e).__name__}"
    ms = int((time.monotonic() - started) * 1000)
    with SessionLocal() as s:
        audit(s, actor, "settings.test", body.target, {"ok": ok, "status": status})
        s.commit()
    return {"ok": ok, "status": status, "latencyMs": ms, "model": model, "message": message}
