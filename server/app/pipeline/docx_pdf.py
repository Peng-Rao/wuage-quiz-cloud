"""本地解析的 Word 没有版面：用 LibreOffice 转成 PDF 供「查看原图」，并按文字在 PDF 中定位各版面单元。"""

import logging
import re
import shutil
import subprocess
import tempfile
import threading
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .ir import Block

log = logging.getLogger(__name__)

# LibreOffice 单个进程内存占用较大，同一进程内逐份转换
_lock = threading.Lock()
TIMEOUT = 180
# 在上一单元之后多远的范围内查找本单元的文字（字符数），避免误配到很远处的相同文字
WINDOW = 4000
KEY = 16
PAD = 0.004


def soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def docx_to_pdf(data: bytes, timeout: int = TIMEOUT) -> bytes | None:
    """转换失败或未安装 LibreOffice 时返回 None（原图不可用，不影响解析）。"""
    exe = soffice()
    if not exe:
        log.warning("未安装 LibreOffice，Word 试卷无法生成原图")
        return None
    with _lock, tempfile.TemporaryDirectory(prefix="docx2pdf-") as tmp:
        d = Path(tmp)
        (d / "in.docx").write_bytes(data)
        # 每次使用独立的配置目录：不依赖 HOME 可写，也不与其他进程的 LibreOffice 冲突
        cmd = [exe, f"-env:UserInstallation={(d / 'profile').as_uri()}", "--headless", "--norestore",
               "--convert-to", "pdf", "--outdir", str(d), str(d / "in.docx")]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            log.warning("Word 转 PDF 超时（%s 秒）", timeout)
            return None
        out = d / "in.pdf"
        if r.returncode != 0 or not out.exists():
            log.warning("Word 转 PDF 失败：%s", (r.stderr or r.stdout).decode(errors="replace")[-500:])
            return None
        return out.read_bytes()


# ---------------- 在 PDF 中定位版面单元 ----------------

_LATEX = re.compile(r"\$\$.+?\$\$|\$.+?\$", re.S)
_TAG = re.compile(r"<[^>]+>")
_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _norm(s: str) -> str:
    """只保留文字与数字（统一全角、大小写），忽略空白、标点与排版差异。"""
    return "".join(c for c in unicodedata.normalize("NFKC", s).lower() if c.isalnum())


def _segments(content: str) -> list[str]:
    """公式在 PDF 中多为图片或不同写法，按公式切开，只用其余文字定位。"""
    text = _TAG.sub(" ", _MD_IMG.sub(" ", content))
    return [n for n in (_norm(x) for x in _LATEX.split(text)) if n]


@dataclass
class _Item:
    page: int  # 从 1 开始
    rect: tuple[float, float, float, float]  # 归一化


@dataclass
class _Layout:
    text: str          # 全文（归一化后）的字符
    chars: list[_Item]  # 与 text 一一对应
    char_pos: list[int]  # 每个字符在全文流中的位置（与图片统一编号）
    images: list[tuple[int, _Item]]  # (流位置, 图片)


def _layout(pdf: bytes) -> _Layout:
    text: list[str] = []
    chars: list[_Item] = []
    char_pos: list[int] = []
    images: list[tuple[int, _Item]] = []
    n = 0
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for pno, page in enumerate(doc, 1):
            w, h = page.rect.width, page.rect.height

            def norm_rect(b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
                return (max(0.0, b[0] / w), max(0.0, b[1] / h), min(1.0, b[2] / w), min(1.0, b[3] / h))

            raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_IMAGES | pymupdf.TEXT_PRESERVE_LIGATURES)
            for block in raw["blocks"]:
                if block["type"] == 1:
                    images.append((n, _Item(pno, norm_rect(block["bbox"]))))
                    n += 1
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        for ch in span["chars"]:
                            c = _norm(ch["c"])
                            if not c:
                                continue
                            item = _Item(pno, norm_rect(ch["bbox"]))
                            for x in c:  # NFKC 可能展开为多个字符
                                text.append(x)
                                chars.append(item)
                                char_pos.append(n)
                            n += 1
    return _Layout("".join(text), chars, char_pos, images)


def _find_span(lay: _Layout, segs: list[str], cursor: int) -> tuple[int, int] | None:
    """在 cursor 之后查找该单元的文字，返回 [start, end) 字符区间。"""
    total = sum(len(s) for s in segs)
    start = -1
    used = ""
    for seg in segs:
        for key in (seg[:KEY], seg[-KEY:]) if len(seg) > KEY else (seg,):
            pos = lay.text.find(key, cursor, cursor + WINDOW + len(key))
            if pos >= 0 and (start < 0 or pos < start):
                start, used = pos, key
        if start >= 0:
            break
    if start < 0:
        return None
    first = segs[0]
    if len(segs) == 1 and lay.text.startswith(first, start):
        return start, start + len(first)
    last = segs[-1][-KEY:]
    pos = lay.text.find(last, start, start + total * 2 + 200)
    end = pos + len(last) if pos >= 0 else start + len(used)
    return start, max(end, start + len(used))


def _union(items: list[_Item]) -> tuple[int, tuple[float, float, float, float]]:
    page = items[0].page
    rs = [i.rect for i in items if i.page == page]
    return page, (min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs))


def locate_blocks(pdf: bytes, blocks: list[Block]) -> int:
    """按 PDF 中的实际位置改写 blocks 的 page / bbox，返回定位到的单元数。

    文字按阅读顺序逐个匹配；图片取前后文字之间的 PDF 图片；其余（公式等）放在前后单元之间。
    """
    lay = _layout(pdf)
    placed: dict[int, tuple[int, tuple[float, float, float, float]]] = {}
    span_pos: dict[int, tuple[int, int]] = {}  # 已定位文字单元在全文流中的 [起, 止] 位置
    cursor = 0
    for i, b in enumerate(blocks):
        if b.type == "image":
            continue
        segs = _segments(b.content)
        if not segs:
            continue
        found = _find_span(lay, segs, cursor)
        if not found:
            continue
        start, end = found
        placed[i] = _union(lay.chars[start:end])
        span_pos[i] = (lay.char_pos[start], lay.char_pos[end - 1])
        cursor = end

    # 图片：取前一个与后一个已定位文字之间、尚未使用的第一张 PDF 图片
    used: set[int] = set()
    img_pos = [p for p, _ in lay.images]
    text_idx = sorted(span_pos)
    for i, b in enumerate(blocks):
        if b.type != "image" or not lay.images:
            continue
        k = bisect_left(text_idx, i)
        lo = span_pos[text_idx[k - 1]][1] if k > 0 else -1
        hi = span_pos[text_idx[k]][0] if k < len(text_idx) else float("inf")
        for j in range(bisect_left(img_pos, lo), len(img_pos)):
            if img_pos[j] >= hi:
                break
            if j not in used and img_pos[j] > lo:
                used.add(j)
                item = lay.images[j][1]
                placed[i] = (item.page, item.rect)
                break

    count = len(placed)
    # 未定位的单元：放在前后已定位单元之间
    order = sorted(placed)
    for i, b in enumerate(blocks):
        if i in placed:
            continue
        k = bisect_left(order, i)
        prev = placed[order[k - 1]] if k > 0 else None
        nxt = placed[order[k]] if k < len(order) else None
        if prev and nxt and prev[0] == nxt[0] and nxt[1][1] > prev[1][3]:
            x0, x1 = min(prev[1][0], nxt[1][0]), max(prev[1][2], nxt[1][2])
            placed[i] = (prev[0], (x0, prev[1][3], x1, nxt[1][1]))
        elif prev or nxt:
            placed[i] = prev or nxt  # type: ignore[assignment]
        else:
            placed[i] = (1, (0.0, 0.0, 1.0, 1.0))
    # 文字单元按整行（正文栏宽）标出：公式、制表位等找不到的部分也在框内
    column: dict[int, tuple[float, float]] = {}
    for c in lay.chars:
        lo, hi = column.get(c.page, (1.0, 0.0))
        column[c.page] = (min(lo, c.rect[0]), max(hi, c.rect[2]))
    for i, b in enumerate(blocks):
        page, (x0, y0, x1, y1) = placed[i]
        if b.type != "image" and page in column:
            x0, x1 = min(x0, column[page][0]), max(x1, column[page][1])
        b.page = page
        b.bbox = (max(0.0, x0 - PAD), max(0.0, y0 - PAD), min(1.0, x1 + PAD), min(1.0, y1 + PAD))
    return count
