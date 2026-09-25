"""轻量本地解析：直接读取 PDF 文字层，不做模型推理。

适合电子版 PDF 的开发调试与兜底；不识别公式和表格，扫描件（无文字层）不可用。
"""

import asyncio
import re
from collections import Counter

import pymupdf

from ..ir import Block, ParsedDocument
from .base import ParserUnavailable, ProgressFn, SourceFile

# 平均每页少于该字符数视为扫描件
MIN_CHARS_PER_PAGE = 30

# 页码行：「试卷第2页（共7页）」「第 3 页 共 8 页」「- 2 -」
PAGE_NO_RE = re.compile(r"^\s*(?:(?:试卷|答案|数学|第)?\S{0,4}第\s*\d+\s*页.{0,8}|[-—]\s*\d+\s*[-—]|\d{1,3})\s*$")


def drop_page_furniture(blocks: list[Block], page_count: int) -> list[Block]:
    """去掉页眉页脚：页码行，以及在多数页面上重复出现的相同短文本（如机构名、水印）。"""
    texts = [b for b in blocks if b.type == "text"]
    pages_of = Counter()
    for text in {(b.page, b.text) for b in texts if len(b.text) <= 30}:
        pages_of[text[1]] += 1
    repeated = {t for t, n in pages_of.items() if page_count >= 2 and n >= max(2, page_count / 2)}
    kept = [b for b in blocks if not (b.type == "text" and (b.text in repeated or PAGE_NO_RE.match(b.text)))]
    for i, b in enumerate(kept, 1):
        b.seq = i
    return kept


def _parse_pdf(data: bytes) -> ParsedDocument:
    doc = pymupdf.open(stream=data, filetype="pdf")
    blocks: list[Block] = []
    total_chars = 0
    seq = 0
    for page in doc:
        w, h = page.rect.width, page.rect.height
        norm = lambda b: (b[0] / w, b[1] / h, b[2] / w, b[3] / h)  # noqa: E731
        info = page.get_text("dict", sort=True)
        for b in info["blocks"]:
            if b["type"] == 1:  # 图片
                seq += 1
                blocks.append(Block(seq, page.number + 1, norm(b["bbox"]), "image", "",
                                    image=b.get("image"), image_ext=b.get("ext", "png")))
                continue
            # 按行输出：题号通常位于行首，行粒度便于切分
            for line in b.get("lines", []):
                text = "".join(s["text"] for s in line["spans"]).strip()
                if not text:
                    continue
                total_chars += len(text)
                seq += 1
                blocks.append(Block(seq, page.number + 1, norm(line["bbox"]), "text", text))
    page_count = doc.page_count
    if page_count and total_chars / page_count < MIN_CHARS_PER_PAGE:
        raise ParserUnavailable("PDF 没有可用的文字层（可能是扫描件），需要 MinerU 进行 OCR")
    return ParsedDocument(blocks=drop_page_furniture(blocks, page_count), page_count=page_count, pdf=data)


class LiteParser:
    name = "lite"

    async def parse(self, src: SourceFile, *, ocr: bool, on_progress: ProgressFn | None = None) -> ParsedDocument:  # noqa: ARG002
        if src.kind != "pdf":
            raise ParserUnavailable("轻量解析仅支持 PDF，Word 请使用 MinerU")
        return await asyncio.to_thread(_parse_pdf, src.data)
