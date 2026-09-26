"""轻量本地解析：直接读取 PDF 文字层，不做模型推理。

适合电子版 PDF：上下标按字号与基线识别（如 O₂、x²），文字中的 x^2、a_n、√(x+1) 转为上下标或 LaTeX；
分式、矩阵等二维公式与表格仍按文字输出，扫描件（无文字层）不可用。
"""

import asyncio
import re
from collections import Counter
from statistics import median

import pymupdf

from ..ir import Block, ParsedDocument
from .base import ParserUnavailable, ProgressFn, SourceFile

# 平均每页少于该字符数视为扫描件
MIN_CHARS_PER_PAGE = 30

# 页码行：「试卷第2页（共7页）」「第 3 页 共 8 页」「- 2 -」
PAGE_NO_RE = re.compile(r"^\s*(?:(?:试卷|答案|数学|第)?\S{0,4}第\s*\d+\s*页.{0,8}|[-—]\s*\d+\s*[-—]|\d{1,3})\s*$")


# ---------------- 上下标与公式 ----------------

_SUP = dict(zip("0123456789+-−=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱ"))
_SUB = dict(zip("0123456789+-−=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"))
# 可作为上下标的小字号文字：字母、数字、正负号等，最多 6 个字符
_SCRIPT_TEXT = re.compile(r"^[A-Za-z0-9+\-−=()′']{1,6}$")


def attach_script(prefix: str, text: str, sup: bool) -> str:
    """在 prefix 末尾接上上标 / 下标：能用 Unicode 上下标字符表示的直接转换（O₂、x²、SO₄²⁻），
    否则把底数并入 LaTeX（$a^{n+1}$），由前端 KaTeX 渲染。"""
    table = _SUP if sup else _SUB
    if all(c in table for c in text):
        return prefix + "".join(table[c] for c in text)
    mark = "^" if sup else "_"
    base = prefix[-1] if prefix and re.match(r"[A-Za-z0-9)\]]", prefix[-1]) else ""
    return prefix[: len(prefix) - len(base)] + f"${base}{mark}{{{text.replace('−', '-')}}}$"


_LINEAR_SUP = re.compile(r"(?<=[A-Za-z0-9)\]}])\^(?:\{([^{}$]+)\}|\(([^()$]+)\)|(-?\d+)|([A-Za-z]))")
_LINEAR_SUB = re.compile(r"(?<=[A-Za-z])_(?:\{([^{}$]+)\}|(\d+)|([a-z]))(?![A-Za-z0-9_])")
_LINEAR_SQRT = re.compile(r"√\(([A-Za-z0-9 +\-*/.,]+)\)")


def linear_math(text: str) -> str:
    """把文字中的线性公式记法转为上下标：x^2 → x²，a_n → aₙ，2^(n+1) → $2^{n+1}$，√(x - 1) → $\\sqrt{x - 1}$。"""
    if "$" in text:
        return text
    text = _LINEAR_SQRT.sub(lambda m: f"$\\sqrt{{{m.group(1).strip()}}}$", text)
    for rx, sup in ((_LINEAR_SUP, True), (_LINEAR_SUB, False)):
        out, last = "", 0
        for m in rx.finditer(text):
            body = next(g for g in m.groups() if g is not None).replace(" ", "")
            out = attach_script(out + text[last:m.start()], body, sup)
            last = m.end()
        text = out + text[last:]
    return text


def line_text(spans: list[dict]) -> str:
    """拼接一行文字，字号明显较小、紧贴前一字符的字母数字识别为上标（基线上移）或下标。"""
    body = [s for s in spans if s["text"].strip()]
    if not body:
        return ""
    size = max(s["size"] for s in body)
    base_y = median(s["origin"][1] for s in body if s["size"] >= size * 0.9)
    out, prev = "", None
    for s in spans:
        t = s["text"]
        st = t.strip()
        if (prev is not None and st and s["size"] < size * 0.8 and _SCRIPT_TEXT.match(st)
                and out and not out[-1].isspace() and s["bbox"][0] - prev["bbox"][2] < size * 0.3):
            out = attach_script(out, st, sup=s["origin"][1] < base_y - size * 0.15)
        else:
            out += t
        prev = s
    return linear_math(out.strip())


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
                text = line_text(line["spans"])
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
