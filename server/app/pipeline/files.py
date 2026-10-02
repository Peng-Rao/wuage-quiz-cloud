"""上传文件的归一化与页面渲染。"""

from pathlib import Path

import pymupdf

from .parsers.base import SourceFile

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
DOC_EXTS = {".pdf", ".docx"}


def kind_of(name: str) -> str | None:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOC_EXTS:
        return ext[1:]
    return None


def validate_kinds(names: list[str]) -> str:
    """一份试卷只能是 1 个 PDF / Word，或 1–N 张图片。返回 pdf / docx / image。"""
    kinds = [kind_of(n) for n in names]
    if any(k is None for k in kinds):
        raise ValueError("仅支持 Word（.docx）、PDF、JPG / PNG 图片")
    if len(kinds) > 1 and any(k != "image" for k in kinds):
        raise ValueError("多个文件仅支持图片（拍照多张），PDF / Word 请逐份上传")
    return kinds[0]  # type: ignore[return-value]


def images_to_pdf(images: list[tuple[str, bytes]]) -> bytes:
    """多张拍照图按顺序合成一份 PDF，每张图一页。"""
    with pymupdf.open() as out:
        for name, data in images:
            with pymupdf.open(stream=data, filetype=Path(name).suffix.lstrip(".").lower() or "png") as img:
                with pymupdf.open("pdf", img.convert_to_pdf()) as page:
                    out.insert_pdf(page)
        return out.tobytes()


def normalize(files: list[tuple[str, bytes]]) -> SourceFile:
    kind = validate_kinds([n for n, _ in files])
    if kind == "image":
        stem = Path(files[0][0]).stem
        return SourceFile(name=f"{stem}.pdf", data=images_to_pdf(files), kind="pdf")
    name, data = files[0]
    return SourceFile(name=name, data=data, kind=kind)


def pdf_page_count(data: bytes) -> int:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return doc.page_count


def render_pages(pdf: bytes, dpi: int, start: int, stop: int) -> list[bytes]:
    """只渲染 [start, stop) 页；文档在本次调用线程中打开并关闭。"""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return [doc[i].get_pixmap(dpi=dpi).tobytes("png") for i in range(start, min(stop, doc.page_count))]
