"""MinerU 云端 API（v4 批量本地文件上传）。

流程：POST /api/v4/file-urls/batch 申请上传地址 → PUT 文件（上传完成即自动开始解析）
→ 轮询 GET /api/v4/extract-results/batch/{batch_id} → 下载 full_zip_url → 读取 content_list.json。
文档：https://mineru.net/apiManage/docs
"""

import asyncio
import io
import json
import time
import uuid
import zipfile
from pathlib import PurePosixPath
from typing import Any

import httpx

from ...config import Settings
from ..ir import Block, BlockType, ParsedDocument
from ..llm import describe
from .base import ParserFailed, ParserUnavailable, ProgressFn, SourceFile

# 页眉、页脚、页码、边注等不参与拆题
_SKIP_TYPES = {"header", "footer", "page_number", "aside_text", "page_footnote", "discarded"}


def _norm_bbox(b: Any) -> tuple[float, float, float, float]:
    if not isinstance(b, (list, tuple)) or len(b) != 4:
        return (0.0, 0.0, 1.0, 1.0)
    return tuple(max(0.0, min(1.0, float(v) / 1000)) for v in b)  # type: ignore[return-value]


def _join(v: Any) -> str:
    if isinstance(v, list):
        return "\n".join(str(x) for x in v if x)
    return str(v or "")


def blocks_from_content_list(items: list[dict[str, Any]], read_image) -> list[Block]:  # noqa: ANN001
    """content_list.json → IR。bbox 为 0–1000 归一化坐标，page_idx 从 0 开始。"""
    blocks: list[Block] = []
    for it in items:
        t = it.get("type", "text")
        if t in _SKIP_TYPES:
            continue
        page = int(it.get("page_idx", 0)) + 1
        bbox = _norm_bbox(it.get("bbox"))
        btype: BlockType = "text"
        content = ""
        image: bytes | None = None
        ext = "png"
        if t == "text":
            content = it.get("text", "")
            btype = "title" if it.get("text_level") else "text"
        elif t == "equation":
            btype, content = "equation", it.get("text", "")
        elif t in ("image", "chart"):
            btype = "image"
            content = _join(it.get("image_caption") or it.get("chart_caption"))
        elif t == "table":
            btype = "table"
            content = it.get("table_body", "") or _join(it.get("table_caption"))
        elif t == "list":
            content = _join(it.get("list_items"))
        elif t == "code":
            content = it.get("code_body", "")
        else:
            content = it.get("text", "") or it.get("content", "")
        if it.get("img_path"):
            image = read_image(it["img_path"])
            ext = PurePosixPath(it["img_path"]).suffix.lstrip(".") or "png"
        if not content.strip() and image is None:
            continue
        blocks.append(Block(len(blocks) + 1, page, bbox, btype, content, image=image, image_ext=ext))
    return blocks


def read_result_zip(data: bytes) -> tuple[list[dict[str, Any]], dict[str, bytes], bytes | None]:
    """返回 (content_list, 图片 {相对路径: 字节}, 原始 PDF 或 None)。"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        cl_name = next((n for n in names if n.endswith("content_list.json") and "_v2" not in n), None)
        if cl_name is None:
            raise ParserFailed("MinerU 结果中缺少 content_list.json")
        base = PurePosixPath(cl_name).parent
        content_list = json.loads(zf.read(cl_name))
        # img_path 相对于 content_list.json 所在目录
        images: dict[str, bytes] = {}
        for n in names:
            p = PurePosixPath(n)
            if n.endswith("/") or "images" not in p.parts:
                continue
            images[str(p.relative_to(base)) if p.is_relative_to(base) else n] = zf.read(n)
        pdf_name = next((n for n in names if n.lower().endswith(".pdf")), None)
        pdf = zf.read(pdf_name) if pdf_name else None
    return content_list, images, pdf


class MinerUCloudParser:
    name = "mineru_cloud"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.s.mineru_token}", "Content-Type": "application/json"}

    async def parse(self, src: SourceFile, *, ocr: bool, on_progress: ProgressFn | None = None) -> ParsedDocument:
        if not self.s.mineru_token:
            raise ParserUnavailable("未配置 MINERU_TOKEN")
        client = self._client or httpx.AsyncClient(timeout=120)
        try:
            return await self._run(client, src, ocr, on_progress)
        except httpx.HTTPError as e:
            try:
                host = e.request.url.host
            except RuntimeError:  # 异常未关联请求
                host = "未知地址"
            raise ParserFailed(f"MinerU 网络错误（{host}）：{describe(e)}") from e
        finally:
            if self._client is None:
                await client.aclose()

    async def _api(self, client: httpx.AsyncClient, method: str, path: str, **kw: Any) -> dict[str, Any]:
        r = await client.request(method, self.s.mineru_base_url + path, headers=self._headers(), **kw)
        if r.status_code in (401, 403):
            raise ParserFailed("MinerU Token 无效或已过期")
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise ParserFailed(f"MinerU 返回错误：{body.get('msg') or body.get('code')}")
        return body["data"]

    async def _run(self, client: httpx.AsyncClient, src: SourceFile, ocr: bool, on_progress: ProgressFn | None) -> ParsedDocument:
        data = await self._api(client, "POST", "/api/v4/file-urls/batch", json={
            "files": [{"name": src.name, "data_id": uuid.uuid4().hex, "is_ocr": ocr}],
            "model_version": self.s.mineru_model_version,
            "enable_formula": True,
            "enable_table": True,
            "language": "ch",
        })
        batch_id, upload_url = data["batch_id"], data["file_urls"][0]

        # 预签名地址按无 Content-Type 签名；httpx 发送 bytes 时不会附加该头
        put = await client.put(upload_url, content=src.data)
        if put.status_code not in (200, 201):
            raise ParserFailed(f"上传到 MinerU 失败（HTTP {put.status_code}）")

        deadline = time.monotonic() + self.s.mineru_timeout
        while True:
            res = await self._api(client, "GET", f"/api/v4/extract-results/batch/{batch_id}")
            item = (res.get("extract_result") or [{}])[0]
            state = item.get("state")
            if state == "done":
                zip_url = item["full_zip_url"]
                break
            if state == "failed":
                raise ParserFailed(f"MinerU 解析失败：{item.get('err_msg') or '未知原因'}")
            prog = item.get("extract_progress") or {}
            if on_progress and prog.get("total_pages"):
                await on_progress(min(0.99, prog.get("extracted_pages", 0) / prog["total_pages"]))
            if time.monotonic() > deadline:
                raise ParserFailed("MinerU 解析超时")
            await asyncio.sleep(self.s.mineru_poll_interval)

        try:
            z = await client.get(zip_url, timeout=300)
            z.raise_for_status()
        except httpx.HTTPError as e:
            raise ParserFailed(
                f"MinerU 已解析完成，但下载结果失败（{httpx.URL(zip_url).host}）：{describe(e)}。"
                "请检查本机网络或代理是否放行该域名"
            ) from e
        content_list, images, pdf = await asyncio.to_thread(read_result_zip, z.content)
        blocks = blocks_from_content_list(content_list, images.get)
        page_count = max((b.page for b in blocks), default=0)
        # 原始文件是 PDF 时直接用原文件渲染页面
        return ParsedDocument(blocks=blocks, page_count=page_count, pdf=src.data if src.kind == "pdf" else pdf)
