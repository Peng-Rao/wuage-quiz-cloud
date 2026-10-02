"""模型池状态与检测。

    uv run python -m app.llm_pool                    # 文字 / 看图模型池、各模型已用 token 与预算
    uv run python -m app.llm_pool --probe            # 逐个检测池中模型能否调用、能否看图
    uv run python -m app.llm_pool --probe m1 m2 ...  # 检测指定模型（用于决定放进哪个池）

看图检测：发送一张写有数字的图片，要求模型读出数字；读对才算支持看图（不支持的模型常会编造答案）。
"""

import argparse
import asyncio
import base64
import json
import time

import httpx

from .config import Settings, get_settings
from .pipeline.llm import _extra_for, _spent


def _digit_image() -> tuple[str, str]:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=160, height=100)
    page.insert_text((30, 70), "37", fontsize=56)
    return "37", "data:image/png;base64," + base64.b64encode(page.get_pixmap(dpi=96).tobytes("png")).decode()


async def _ask(client: httpx.AsyncClient, s: Settings, model: str, content, base_extra: dict) -> tuple[str, str]:  # noqa: ANN001
    body = {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0, "stream": True,
            "stream_options": {"include_usage": True}, **_extra_for(model, base_extra, s)}
    out: list[str] = []
    try:
        async with client.stream("POST", s.llm_base_url.rstrip("/") + "/chat/completions", json=body,
                                 headers={"Authorization": f"Bearer {s.llm_api_key}"}) as r:
            if r.status_code != 200:
                await r.aread()
                return "error", f"HTTP {r.status_code} {r.text[:120]}"
            async for line in r.aiter_lines():
                if line.startswith("data:") and line[5:].strip() != "[DONE]":
                    for ch in json.loads(line[5:]).get("choices") or []:
                        out.append((ch.get("delta") or {}).get("content") or "")
    except httpx.HTTPError as e:
        return "error", f"{type(e).__name__}: {e}"
    return "ok", "".join(out).strip()


async def probe(models: list[str], s: Settings) -> None:
    digits, image = _digit_image()
    async with httpx.AsyncClient(timeout=180) as client:
        for m in models:
            t0 = time.monotonic()
            st, text = await _ask(client, s, m, "只回复两个字：好的", s.llm_extra_body)
            vision_extra = s.llm_extra_body if s.vision_extra_body is None else s.vision_extra_body
            vs, vtext = await _ask(client, s, m, [{"type": "text", "text": "图片中的数字是多少？只回复数字"},
                                                  {"type": "image_url", "image_url": {"url": image}}], vision_extra)
            vision = "支持" if vs == "ok" and digits in vtext else "不支持"
            print(f"{m:30} 文字：{'可用' if st == 'ok' else '不可用'}  看图：{vision}  ({time.monotonic() - t0:.1f}s)"
                  + (f"\n{'':30} {text if st != 'ok' else vtext[:80]}" if st != "ok" or vision == "不支持" else ""),
                  flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", nargs="*", metavar="MODEL", help="检测模型；不指定时检测池中全部模型")
    args = ap.parse_args()
    s = get_settings()
    if args.probe is not None:
        models = args.probe or list(dict.fromkeys(s.text_models + s.vision_model_pool))
        asyncio.run(probe(models, s))
        return
    since = f"（自 {s.llm_budget_since:%Y-%m-%d %H:%M} 起）" if s.llm_budget_since else ""
    for title, pool in (("文字模型池", s.text_models), ("看图模型池", s.vision_model_pool)):
        print(f"{title}：")
        for i, m in enumerate(pool, 1):
            budget = s.llm_model_budget.get(m)
            used = _spent(m, s)
            extra = s.llm_model_extra_body.get(m)
            print(f"  {i}. {m:30} 已用 {used:>9,} tokens{since}"
                  + (f" / 预算 {budget:,}（剩 {max(budget - used, 0):,}）" if budget is not None else "")
                  + (f"  附加参数 {json.dumps(extra, ensure_ascii=False)}" if extra else ""))
        if not pool:
            print("  （未配置）")


if __name__ == "__main__":
    main()
