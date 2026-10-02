"""Word（.docx）本地解析：直接读取文档中的文字、图片、表格与公式，不经 MinerU。

- MathType 公式（OLE 对象、粘贴为图片的 WMF）经 MTEF 转为 LaTeX，Word 原生公式（OMML）同样转为 LaTeX，
  行内以 $...$ 写入文字；无法转换的公式较多时放弃本地解析，交给解析链中的下一个引擎（MinerU）。
- 自动编号还原为文字（题号常用自动编号）；下划线空白还原为 ____；上下标转为 $^{..}$ / $_{..}$。
- Word 没有固定分页，页码按文档中的分页符估算；不生成「查看原图」用的 PDF。
"""

import asyncio
import html
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from ..ir import Block, ParsedDocument
from . import mtef, omml
from .base import ParserFailed, ParserUnavailable, ProgressFn, SourceFile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
V = "{urn:schemas-microsoft-com:vml}"
OFFICE = "{urn:schemas-microsoft-com:office:office}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# 公式转换失败超过该比例（且不少于 3 处）时放弃本地解析
MAX_FORMULA_FAIL_RATIO = 0.05
# 全文文字少于该字数且含图片时，视为扫描件 / 截图，交给 MinerU 识别
MIN_TEXT_CHARS = 80
EMU_PER_PT = 12700

# Symbol 字体（w:sym）常见字符
SYMBOL_FONT = {0xB0: "°", 0xB4: "×", 0xB1: "±", 0xB3: "≥", 0xA3: "≤", 0xB9: "≠", 0xD0: "∠", 0x70: "π",
               0x61: "α", 0x62: "β", 0x67: "γ", 0x64: "δ", 0x6C: "λ", 0x6D: "μ", 0x77: "ω", 0x57: "Ω",
               0xAE: "→", 0xDE: "⇒", 0x5E: "⊥", 0xBA: "≡", 0xBB: "≈", 0xB7: "•", 0xD6: "√", 0x44: "Δ"}
OPTION_CELL_RE = re.compile(r"^[A-HＡ-Ｈ]\s*[.．、:：]")
CN_DIGITS = "零一二三四五六七八九"


def _cn_number(n: int) -> str:
    if n < 10:
        return CN_DIGITS[n]
    if n < 20:
        return "十" + (CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return CN_DIGITS[n // 10] + "十" + (CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


def _roman(n: int) -> str:
    out = ""
    for v, s in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                 (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + s, n - v
    return out


def _letters(n: int) -> str:
    return chr(ord("A") + (n - 1) % 26) * ((n - 1) // 26 + 1)


def format_number(n: int, fmt: str) -> str:
    if fmt in ("upperLetter",):
        return _letters(n)
    if fmt == "lowerLetter":
        return _letters(n).lower()
    if fmt == "upperRoman":
        return _roman(n)
    if fmt == "lowerRoman":
        return _roman(n).lower()
    if fmt in ("chineseCounting", "chineseCountingThousand", "chineseLegalSimplified", "ideographTraditional",
               "taiwaneseCounting", "japaneseCounting"):
        return _cn_number(n)
    if fmt in ("decimalEnclosedCircle", "decimalEnclosedCircleChinese") and 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt == "decimalFullWidth":
        return "".join(chr(ord(c) + 0xFEE0) for c in str(n))
    return str(n)


@dataclass
class _Level:
    fmt: str
    text: str
    start: int


class _Numbering:
    """numbering.xml：按 numId / 级别计数，生成自动编号文字。项目符号不输出。"""

    def __init__(self, xml: bytes | None):
        self.nums: dict[str, dict[int, _Level]] = {}
        self.counters: dict[str, list[int]] = {}
        if not xml:
            return
        root = ET.fromstring(xml)
        abstract: dict[str, dict[int, _Level]] = {}
        for an in root.findall(W + "abstractNum"):
            levels = {}
            for lvl in an.findall(W + "lvl"):
                ilvl = int(lvl.get(W + "ilvl", "0"))
                fmt = lvl.find(W + "numFmt")
                txt = lvl.find(W + "lvlText")
                start = lvl.find(W + "start")
                levels[ilvl] = _Level(fmt.get(W + "val", "decimal") if fmt is not None else "decimal",
                                      txt.get(W + "val", "") if txt is not None else "",
                                      int(start.get(W + "val", "1")) if start is not None else 1)
            abstract[an.get(W + "abstractNumId", "")] = levels
        for num in root.findall(W + "num"):
            ref = num.find(W + "abstractNumId")
            levels = dict(abstract.get(ref.get(W + "val", "") if ref is not None else "", {}))
            for ov in num.findall(W + "lvlOverride"):
                so = ov.find(W + "startOverride")
                ilvl = int(ov.get(W + "ilvl", "0"))
                if so is not None and ilvl in levels:
                    lv = levels[ilvl]
                    levels[ilvl] = _Level(lv.fmt, lv.text, int(so.get(W + "val", "1")))
            self.nums[num.get(W + "numId", "")] = levels

    def label(self, num_id: str, ilvl: int) -> str:
        levels = self.nums.get(num_id)
        if not levels or ilvl not in levels:
            return ""
        counters = self.counters.setdefault(num_id, [lv.start - 1 for lv in (levels.get(i) for i in range(9))
                                                     if lv] + [0] * 9)
        counters[ilvl] += 1
        for deeper in range(ilvl + 1, 9):
            if deeper in levels:
                counters[deeper] = levels[deeper].start - 1
        lv = levels[ilvl]
        if lv.fmt in ("bullet", "none") or not lv.text:
            return ""

        def sub(m: re.Match) -> str:
            k = int(m.group(1)) - 1
            if k not in levels:
                return ""
            return format_number(max(counters[k], levels[k].start), levels[k].fmt)

        return re.sub(r"%(\d)", sub, lv.text)


@dataclass
class _Img:
    data: bytes
    ext: str


class _Doc:
    def __init__(self, z: zipfile.ZipFile):
        self.z = z
        self.rels = self._rels("word/_rels/document.xml.rels")
        self.numbering = _Numbering(self._read("word/numbering.xml"))
        self.heading_styles = self._heading_styles()
        self.page = 1
        self.blocks: list[Block] = []
        self.formula_ok = 0
        self.formula_fail = 0
        self.skipped_images = 0
        self.text_chars = 0
        self.image_count = 0

    # ---------- 包内文件 ----------

    def _read(self, name: str) -> bytes | None:
        try:
            return self.z.read(name)
        except KeyError:
            return None

    def _rels(self, name: str) -> dict[str, str]:
        data = self._read(name)
        if not data:
            return {}
        out = {}
        for rel in ET.fromstring(data).findall(PKG_REL + "Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            path = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("word") / target)
            out[rel.get("Id", "")] = str(PurePosixPath(path))
        return out

    def part(self, rid: str | None) -> tuple[bytes, str] | None:
        if not rid or rid not in self.rels:
            return None
        name = self.rels[rid]
        # 规范化 word/../xx
        parts: list[str] = []
        for seg in name.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg and seg != ".":
                parts.append(seg)
        data = self._read("/".join(parts))
        return (data, PurePosixPath(name).suffix.lower().lstrip(".")) if data is not None else None

    def _heading_styles(self) -> set[str]:
        data = self._read("word/styles.xml")
        out: set[str] = set()
        if not data:
            return out
        for st in ET.fromstring(data).findall(W + "style"):
            name = st.find(W + "name")
            n = (name.get(W + "val", "") if name is not None else "").lower()
            if n.startswith(("heading", "title")) or n.startswith("标题"):
                out.add(st.get(W + "styleId", ""))
        return out

    # ---------- 公式与图片 ----------

    def formula(self, tex: str | None) -> str:
        if tex is None:
            self.formula_fail += 1
            return "[公式]"
        self.formula_ok += 1
        tex = tex.strip()
        return f"${tex}$" if tex else ""

    def mtef_ole(self, ole_rid: str | None, preview_rid: str | None) -> str:
        for rid, reader in ((ole_rid, mtef.from_ole), (preview_rid, mtef.from_wmf)):
            p = self.part(rid)
            if not p:
                continue
            try:
                return self.formula(mtef.to_latex(reader(p[0])))
            except Exception:  # noqa: BLE001, S112 — 依次尝试 OLE 与预览图
                continue
        return self.formula(None)

    def image(self, rid: str | None) -> "_Img | str | None":
        """图片：位图返回 _Img；MathType 的 WMF 返回公式文字；无法显示的矢量图返回 None。"""
        p = self.part(rid)
        if not p:
            return None
        data, ext = p
        if ext in ("wmf", "emf"):
            try:
                return self.formula(mtef.to_latex(mtef.from_wmf(data)))
            except Exception:  # noqa: BLE001
                self.skipped_images += 1
                return None
        if ext in ("jpg", "jpeg"):
            return _Img(data, "jpeg")
        if ext == "png":
            return _Img(data, "png")
        try:
            import pymupdf

            return _Img(pymupdf.Pixmap(data).tobytes("png"), "png")
        except Exception:  # noqa: BLE001
            self.skipped_images += 1
            return None

    # ---------- 段落 ----------

    def runs(self, el: ET.Element, out: list, fields: list[str]) -> None:  # noqa: C901, PLR0912
        """按文档顺序把段落内容追加到 out：str 为文字，_Img 为图片，'\\n' 为换行。"""
        for c in el:
            tag = c.tag
            if tag == W + "r":
                self.run(c, out, fields)
            elif tag in (W + "hyperlink", W + "smartTag", W + "customXml", W + "ins", W + "fldSimple", W + "bdo",
                         W + "dir", W + "moveTo"):
                self.runs(c, out, fields)
            elif tag == W + "sdt":
                content = c.find(W + "sdtContent")
                if content is not None:
                    self.runs(content, out, fields)
            elif tag == M + "oMath":
                if "instr" not in fields:
                    out.append(self.formula(_safe(omml.to_latex, c)))
            elif tag == M + "oMathPara":
                if "instr" not in fields:
                    for mm in c.iter(M + "oMath"):
                        out.append("\n")
                        out.append(self.formula(_safe(omml.to_latex, mm)))
                        out.append("\n")
            elif tag == MC + "AlternateContent":
                choice = c.find(MC + "Choice")
                self.runs(choice if choice is not None else c, out, fields)

    def run(self, r: ET.Element, out: list, fields: list[str]) -> None:  # noqa: C901, PLR0912, PLR0915
        rpr = r.find(W + "rPr")
        hidden = underline = False
        vert = None
        if rpr is not None:
            van = rpr.find(W + "vanish")
            hidden = van is not None and van.get(W + "val", "true") not in ("0", "false", "off")
            u = rpr.find(W + "u")
            underline = u is not None and u.get(W + "val", "single") != "none"
            va = rpr.find(W + "vertAlign")
            vert = va.get(W + "val") if va is not None else None
        for c in r:
            tag = c.tag
            if tag == W + "fldChar":
                kind = c.get(W + "fldCharType")
                if kind == "begin":
                    fields.append("instr")
                elif kind == "separate" and fields:
                    fields[-1] = "result"
                elif kind == "end" and fields:
                    fields.pop()
                continue
            if "instr" in fields or hidden or tag == W + "instrText" or tag == W + "delText":
                continue
            if tag == W + "t":
                text = (c.text or "").replace("$", "＄")
                if underline and not text.strip("  　\t"):
                    if not (out and isinstance(out[-1], str) and out[-1].endswith("____")):
                        out.append("____")
                elif vert in ("superscript", "subscript") and text.strip():
                    mark = "^" if vert == "superscript" else "_"
                    out.append(f"${mark}{{{_escape_tex(text.strip())}}}$")
                else:
                    out.append(text)
            elif tag == W + "tab":
                out.append("____" if underline else " ")
            elif tag in (W + "br", W + "cr"):
                if c.get(W + "type") == "page":
                    self.page += 1
                out.append("\n")
            elif tag == W + "lastRenderedPageBreak":
                self.page += 1
            elif tag == W + "noBreakHyphen":
                out.append("-")
            elif tag == W + "sym":
                code = int(c.get(W + "char", "0"), 16) & 0xFF
                font = (c.get(W + "font") or "").lower()
                if "symbol" in font and code in SYMBOL_FONT:
                    out.append(SYMBOL_FONT[code])
            elif tag == W + "object":
                self.ole(c, out)
            elif tag in (W + "drawing", W + "pict"):
                self.graphic(c, out)
            elif tag == MC + "AlternateContent":
                choice = c.find(MC + "Choice")
                tmp = ET.Element("r")
                tmp.extend(list(choice if choice is not None else c))
                self.run(tmp, out, fields)
            elif tag == W + "ruby":
                base = c.find(W + "rubyBase")
                if base is not None:
                    self.runs(base, out, fields)

    def ole(self, obj: ET.Element, out: list) -> None:
        ole = obj.find(f".//{OFFICE}OLEObject")
        preview = obj.find(f".//{V}imagedata")
        preview_rid = preview.get(R + "id") if preview is not None else None
        prog = (ole.get("ProgID") or "") if ole is not None else ""
        if prog.startswith("Equation."):
            out.append(self.mtef_ole(ole.get(R + "id") if ole is not None else None, preview_rid))
            return
        img = self.image(preview_rid)
        if img is not None:
            out.append(img)

    def graphic(self, el: ET.Element, out: list) -> None:
        if el.find(f".//{OFFICE}OLEObject") is not None:
            self.ole(el, out)
            return
        rids = [b.get(R + "embed") for b in el.iter(A + "blip")] + [v.get(R + "id") for v in el.iter(V + "imagedata")]
        for rid in rids:
            img = self.image(rid)
            if img is not None:
                out.append(img)

    def paragraph_items(self, p: ET.Element) -> list:
        out: list = []
        ppr = p.find(W + "pPr")
        if ppr is not None:
            num = ppr.find(W + "numPr")
            if num is not None:
                nid, lvl = num.find(W + "numId"), num.find(W + "ilvl")
                label = self.numbering.label(nid.get(W + "val", "") if nid is not None else "",
                                             int(lvl.get(W + "val", "0")) if lvl is not None else 0)
                if label:
                    out.append(label + " ")
            if ppr.find(W + "pageBreakBefore") is not None:
                self.page += 1
        self.runs(p, out, [])
        return out

    # ---------- 输出 Block ----------

    def emit_items(self, items: list, title: bool = False) -> None:
        """段落内容 → Block：换行与图片处断开，便于按行识别题号。"""
        buf: list[str] = []

        def flush() -> None:
            text = _clean("".join(buf))
            buf.clear()
            if text:
                self.text_chars += len(text)
                self.add("title" if title else "text", text)

        for it in items:
            if isinstance(it, _Img):
                flush()
                self.image_count += 1
                self.add("image", "", it)
            elif it == "\n":
                flush()
            else:
                buf.append(it)
        flush()

    def add(self, btype: str, content: str, img: _Img | None = None) -> None:
        self.blocks.append(Block(len(self.blocks) + 1, self.page, (0, 0, 1, 1), btype, content,  # type: ignore[arg-type]
                                 image=img.data if img else None, image_ext=img.ext if img else "png"))

    def paragraph(self, p: ET.Element) -> None:
        ppr = p.find(W + "pPr")
        style = ppr.find(W + "pStyle") if ppr is not None else None
        title = style is not None and style.get(W + "val", "") in self.heading_styles
        self.emit_items(self.paragraph_items(p), title)

    def table(self, tbl: ET.Element) -> None:  # noqa: C901
        rows: list[list[tuple[str, int, list[_Img]]]] = []
        for tr in tbl.findall(W + "tr"):
            row = []
            for tc in tr.findall(W + "tc"):
                tcpr = tc.find(W + "tcPr")
                span, merge = 1, None
                if tcpr is not None:
                    gs = tcpr.find(W + "gridSpan")
                    span = int(gs.get(W + "val", "1")) if gs is not None else 1
                    vm = tcpr.find(W + "vMerge")
                    merge = (vm.get(W + "val") or "continue") if vm is not None else None
                texts, imgs = [], []
                for p in tc.iter(W + "p"):
                    items = self.paragraph_items(p)
                    imgs += [x for x in items if isinstance(x, _Img)]
                    t = _clean("".join(x if isinstance(x, str) else "" for x in items).replace("\n", " "))
                    if t:
                        texts.append(t)
                row.append((" ".join(texts), span, imgs, merge))
            rows.append(row)
        cells = [c for row in rows for c in row]
        texts = [c[0] for c in cells if c[0]]
        images = [img for c in cells for img in c[2]]
        ncols = max((sum(c[1] for c in row) for row in rows), default=0)
        # 单列 / 单格表格多为排版用的边框；选项表格（A. B. C. D.）按行输出文字
        if ncols <= 1 or (texts and all(OPTION_CELL_RE.match(t) for t in texts)):
            for row in rows:
                line = "  ".join(c[0] for c in row if c[0])
                if line:
                    self.emit_items([line])
            for img in images:
                self.emit_items([img])
            return
        # 纵向合并：continue 的单元格并入上方，统计 rowspan
        html_rows: list[list[list]] = []
        col_owner: dict[int, list] = {}
        for row in rows:
            out_row, col = [], 0
            for text, span, _imgs, merge in row:
                if merge == "continue" and col in col_owner:
                    col_owner[col][2] += 1
                else:
                    cell = [text, span, 1]
                    out_row.append(cell)
                    for k in range(col, col + span):
                        col_owner[k] = cell
                col += span
            html_rows.append(out_row)
        body = "".join(
            "<tr>" + "".join(
                "<td" + (f' colspan="{s}"' if s > 1 else "") + (f' rowspan="{rs}"' if rs > 1 else "") + ">"
                + html.escape(t, quote=False) + "</td>" for t, s, rs in r) + "</tr>"
            for r in html_rows if r)
        if body:
            self.text_chars += sum(len(t) for t in texts)
            self.add("table", f"<table>{body}</table>")
        for img in images:
            self.emit_items([img])

    def body(self, el: ET.Element) -> None:
        for c in el:
            if c.tag == W + "p":
                self.paragraph(c)
            elif c.tag == W + "tbl":
                self.table(c)
            elif c.tag == W + "sdt":
                content = c.find(W + "sdtContent")
                if content is not None:
                    self.body(content)
            elif c.tag in (W + "customXml", W + "ins"):
                self.body(c)
            elif c.tag == MC + "AlternateContent":
                choice = c.find(MC + "Choice")
                self.body(choice if choice is not None else c)


def _safe(fn, *args):  # noqa: ANN001, ANN002, ANN202
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return None


def _escape_tex(s: str) -> str:
    return re.sub(r"([\\{}#%&_^])", r"\\\1", s)


def _clean(s: str) -> str:
    s = s.replace(" ", " ").replace("​", "")
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"_{5,}", "____", s)
    return s.strip()


def parse_docx(data: bytes) -> ParsedDocument:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ParserFailed("不是有效的 Word（.docx）文件") from e
    doc = _Doc(z)
    root = ET.fromstring(xml)
    body = root.find(W + "body")
    if body is None:
        raise ParserFailed("Word 文档没有正文")
    doc.body(body)

    if doc.text_chars < MIN_TEXT_CHARS and doc.image_count:
        raise ParserUnavailable("Word 中主要是图片（可能是扫描件），需要 MinerU 进行 OCR")
    total = doc.formula_ok + doc.formula_fail
    if doc.formula_fail >= 3 and doc.formula_fail > total * MAX_FORMULA_FAIL_RATIO:
        raise ParserFailed(f"{doc.formula_fail}/{total} 个公式无法转换")
    warnings = []
    if doc.formula_fail:
        warnings.append(f"{doc.formula_fail} 个公式无法转换，已用 [公式] 占位")
    if doc.skipped_images:
        warnings.append(f"{doc.skipped_images} 张 WMF / EMF 等格式的图片无法显示，已跳过")

    # 无版面坐标：同一页内按阅读顺序纵向均分，供题目区域排序使用
    by_page: dict[int, list[Block]] = {}
    for b in doc.blocks:
        by_page.setdefault(b.page, []).append(b)
    for blocks in by_page.values():
        n = len(blocks)
        for i, b in enumerate(blocks):
            b.bbox = (0.0, i / n, 1.0, (i + 1) / n)
    pages = sorted(by_page)
    # 页码连续化（分页符估算可能跳号）
    remap = {p: i + 1 for i, p in enumerate(pages)}
    for b in doc.blocks:
        b.page = remap[b.page]
    return ParsedDocument(blocks=doc.blocks, page_count=len(pages) or 1, pdf=None, warnings=warnings)


class DocxParser:
    name = "docx"

    async def parse(self, src: SourceFile, *, ocr: bool, on_progress: ProgressFn | None = None) -> ParsedDocument:  # noqa: ARG002
        if src.kind != "docx":
            raise ParserUnavailable("本地 Word 解析仅支持 .docx")
        try:
            return await asyncio.to_thread(parse_docx, src.data)
        except (ParserUnavailable, ParserFailed):
            raise
        except Exception as e:  # noqa: BLE001 — 文档结构异常等无法识别的情况，交给解析链中的下一个引擎（MinerU）
            raise ParserFailed(f"本地 Word 解析出错：{type(e).__name__}: {e}") from e
