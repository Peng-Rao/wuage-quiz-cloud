"""试卷原件的 PDF：供试卷库、试卷解析页预览原卷。

- PDF 试卷：原始文件。
- Word 试卷：解析时用 LibreOffice 转成的 PDF（保存在 jobs/<id>/original.pdf）；早期任务没有保存时，预览时再转换一次并保存。
  未安装 LibreOffice 或转换失败时，用解析时渲染的页面图合成。
- 拍照上传：按顺序把原图合成一份 PDF，每张一页。
"""

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .db import ParseJob
from .pipeline.docx_pdf import docx_to_pdf
from .pipeline.files import images_to_pdf
from .storage import ObjectStore, get_store

log = logging.getLogger(__name__)

MAX_PAGES = 200


class NoOriginal(Exception):
    """原始文件不存在或无法生成 PDF。"""


@dataclass
class OriginalPdf:
    file_name: str
    data: bytes | None = None
    # 本地存储的原始 PDF 直接按路径返回，不读入内存
    path: Path | None = None
    # 由 Word / 图片转换而来（排版以原件为准）
    converted: bool = False


def cache_key(job_id: str) -> str:
    return f"jobs/{job_id}/original.pdf"


def pdf_name(file_name: str) -> str:
    return file_name if file_name.lower().endswith(".pdf") else f"{PurePosixPath(file_name).stem}.pdf"


def _pages_pdf(store: ObjectStore, job: ParseJob) -> bytes | None:
    """解析时渲染的页面图合成 PDF。"""
    pages = []
    for i in range(1, min(job.page_count or MAX_PAGES, MAX_PAGES) + 1):
        key = f"jobs/{job.id}/pages/{i}.png"
        if not store.exists(key):
            break
        pages.append((f"{i}.png", store.get(key)))
    if not pages:
        return None
    try:
        return images_to_pdf(pages)
    except Exception as e:  # noqa: BLE001  页面图损坏时视为没有页面图
        log.warning("用页面图合成原卷 PDF 失败（%s）：%s", job.id, e)
        return None


def original_pdf(job: ParseJob) -> OriginalPdf:
    from .storage import LocalStore

    store = get_store()
    keys = [f["key"] for f in job.file_keys or []]
    if not keys or any(not k.startswith("uploads/") for k in keys):
        raise NoOriginal("原始文件不存在")
    name = pdf_name(job.file_name)

    if job.file_type == "pdf":
        if len(keys) != 1 or not store.exists(keys[0]):
            raise NoOriginal("原始 PDF 文件不存在")
        if isinstance(store, LocalStore):
            return OriginalPdf(name, path=store.path(keys[0]))
        return OriginalPdf(name, data=store.get(keys[0]))

    if job.file_type == "image":
        if not all(store.exists(k) for k in keys):
            raise NoOriginal("原始图片不存在")
        files = [(f.get("name") or f["key"], store.get(f["key"])) for f in job.file_keys]
        return OriginalPdf(name, data=images_to_pdf(files), converted=True)

    if job.file_type == "docx":
        cached = cache_key(job.id)
        if store.exists(cached):
            return OriginalPdf(name, data=store.get(cached), converted=True)
        if len(keys) == 1 and store.exists(keys[0]):
            pdf = docx_to_pdf(store.get(keys[0]))
            if pdf:
                store.put(cached, pdf)
                return OriginalPdf(name, data=pdf, converted=True)
        pages = _pages_pdf(store, job)
        if pages:
            return OriginalPdf(name, data=pages, converted=True)
        raise NoOriginal("无法把这份 Word 试卷转换为 PDF（服务器未安装 LibreOffice，且没有页面图）")

    raise NoOriginal("不支持预览该类型的原卷")
