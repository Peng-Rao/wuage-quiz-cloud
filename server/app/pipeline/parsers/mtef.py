"""MathType 公式（MTEF v5）→ LaTeX。

Word 里的 MathType 公式是 OLE 对象（流「Equation Native」= 28 字节头 + MTEF），粘贴成图片的公式是 WMF，
其中的 AppsMFCC 注释记录同样携带 MTEF。格式说明：https://docs.wiris.com/en_US/mathtype-mtef-v5-mathtype-40-and-later
"""

import io
import struct
from dataclasses import dataclass, field


class MTEFError(Exception):
    pass


# ---------------- 记录结构 ----------------

END, LINE, CHAR, TMPL, PILE, MATRIX, EMBELL, RULER, FONT_STYLE_DEF, SIZE = range(10)
FULL, SUB, SUB2, SYM, SUBSYM, COLOR, COLOR_DEF, FONT_DEF, EQN_PREFS, ENCODING_DEF = range(10, 20)

OPT_NUDGE = 0x08
OPT_CHAR_EMBELL = 0x01
OPT_CHAR_FUNC_START = 0x02
OPT_CHAR_ENC_CHAR_8 = 0x04
OPT_CHAR_ENC_CHAR_16 = 0x10
OPT_CHAR_ENC_NO_MTCODE = 0x20
OPT_LINE_NULL = 0x01
OPT_LP_RULER = 0x02
OPT_LINE_LSPACE = 0x04
OPT_COLOR_CMYK = 0x01
OPT_COLOR_NAME = 0x04

FN_TEXT, FN_FUNCTION, FN_VARIABLE, FN_LCGREEK, FN_UCGREEK, FN_SYMBOL, FN_VECTOR, FN_NUMBER = range(1, 9)
FN_MTEXTRA, FN_TEXT_FE, FN_EXPAND, FN_MARKER, FN_SPACE = 11, 12, 22, 23, 24


@dataclass
class Char:
    code: int
    typeface: int
    func_start: bool = False
    embells: list[int] = field(default_factory=list)


@dataclass
class Line:
    items: list  # Char | Tmpl | Pile | Matrix
    null: bool = False


@dataclass
class Tmpl:
    selector: int
    variation: int
    slots: list  # Line | Pile | Matrix | Char（模板中的括号等字符）


@dataclass
class Pile:
    halign: int
    lines: list


@dataclass
class Matrix:
    rows: int
    cols: int
    cells: list


class _Reader:
    def __init__(self, data: bytes):
        self.b = io.BytesIO(data)

    def u8(self) -> int:
        c = self.b.read(1)
        if not c:
            raise MTEFError("数据意外结束")
        return c[0]

    def u16(self) -> int:
        lo, hi = self.u8(), self.u8()
        return lo | (hi << 8)

    def uint(self) -> int:
        v = self.u8()
        return self.u16() if v == 255 else v

    def cstr(self) -> str:
        out = bytearray()
        while (c := self.u8()) != 0:
            out.append(c)
        return out.decode("latin-1")

    def skip(self, n: int) -> None:
        if len(self.b.read(n)) != n:
            raise MTEFError("数据意外结束")

    def peek(self) -> int | None:
        pos = self.b.tell()
        c = self.b.read(1)
        self.b.seek(pos)
        return c[0] if c else None


def _nudge(r: _Reader, opts: int) -> None:
    if opts & OPT_NUDGE:
        dx, dy = r.u8(), r.u8()
        if dx == 128 and dy == 128:
            r.skip(4)


def _ruler(r: _Reader) -> None:
    """LINE / PILE 中的制表位：实际数据不带 RULER 记录类型字节，直接是数量 + 每个 3 字节。"""
    r.skip(r.u8() * 3)


def _dim_array(r: _Reader) -> None:
    """尺寸数组：数量 + 半字节流（每个值：单位半字节，数字半字节……以 0xF 结束）。"""
    count = r.u8()
    nibbles: list[int] = []

    def nib() -> int:
        if not nibbles:
            b = r.u8()
            nibbles.extend([b >> 4, b & 0xF])
        return nibbles.pop(0)

    for _ in range(count):
        nib()  # 单位
        while nib() != 0xF:
            pass


def _skip_record(r: _Reader, tag: int) -> None:
    """跳过不影响公式结构的记录（定义、尺寸、颜色等）。"""
    if tag == FONT_STYLE_DEF:
        r.uint()
        r.u8()
    elif tag == SIZE:
        v = r.u8()
        if v == 101:
            r.u16()
        elif v == 100:
            r.u8()
            r.u16()
        else:
            r.u8()
    elif FULL <= tag <= SUBSYM:
        pass
    elif tag == COLOR:
        r.uint()
    elif tag == COLOR_DEF:
        opts = r.u8()
        r.skip(2 * (4 if opts & OPT_COLOR_CMYK else 3))
        if opts & OPT_COLOR_NAME:
            r.cstr()
    elif tag == FONT_DEF:
        r.uint()
        r.cstr()
    elif tag == EQN_PREFS:
        r.u8()
        _dim_array(r)
        _dim_array(r)
        for _ in range(r.u8()):
            if r.u8():
                r.u8()
    elif tag == ENCODING_DEF:
        r.cstr()
    elif tag == RULER:
        r.skip(r.u8() * 3)
    elif tag >= 100:
        r.skip(r.uint())
    else:
        raise MTEFError(f"未知记录类型 {tag}")


def _objects(r: _Reader) -> list:
    """读取对象列表直到 END。"""
    out: list = []
    while True:
        tag = r.u8()
        if tag == END:
            return out
        obj = _object(r, tag)
        if obj is not None:
            out.append(obj)


def _object(r: _Reader, tag: int):  # noqa: ANN202
    if tag == LINE:
        opts = r.u8()
        _nudge(r, opts)
        if opts & OPT_LINE_LSPACE:
            r.u16()
        if opts & OPT_LP_RULER:
            _ruler(r)
        if opts & OPT_LINE_NULL:
            return Line([], null=True)
        return Line(_objects(r))
    if tag == CHAR:
        opts = r.u8()
        _nudge(r, opts)
        typeface = r.u8() - 128
        code = 0
        if not opts & OPT_CHAR_ENC_NO_MTCODE:
            code = r.u16()
        if opts & OPT_CHAR_ENC_CHAR_8:
            pos = r.u8()
            code = code or pos
        if opts & OPT_CHAR_ENC_CHAR_16:
            pos = r.u16()
            code = code or pos
        ch = Char(code, typeface, bool(opts & OPT_CHAR_FUNC_START))
        if opts & OPT_CHAR_EMBELL:
            for e in _objects(r):
                if isinstance(e, int):
                    ch.embells.append(e)
        return ch
    if tag == TMPL:
        opts = r.u8()
        _nudge(r, opts)
        selector = r.uint()
        variation = r.u8()
        if variation & 0x80:
            variation = (variation & 0x7F) | (r.u8() << 8)
        r.u8()  # 模板专用选项
        return Tmpl(selector, variation, _objects(r))
    if tag == PILE:
        opts = r.u8()
        _nudge(r, opts)
        halign = r.u8()
        r.u8()  # valign
        if opts & OPT_LP_RULER:
            _ruler(r)
        return Pile(halign, [x for x in _objects(r) if isinstance(x, Line)])
    if tag == MATRIX:
        opts = r.u8()
        _nudge(r, opts)
        r.u8()  # valign
        r.u8()  # h_just
        r.u8()  # v_just
        rows, cols = r.u8(), r.u8()
        r.skip(((rows + 1) * 2 + 7) // 8)
        r.skip(((cols + 1) * 2 + 7) // 8)
        return Matrix(rows, cols, [x for x in _objects(r) if isinstance(x, Line)])
    if tag == EMBELL:
        opts = r.u8()
        _nudge(r, opts)
        return r.u8()
    _skip_record(r, tag)
    return None


def parse(mtef: bytes) -> list:
    """MTEF 字节 → 顶层对象列表。"""
    r = _Reader(mtef)
    version = r.u8()
    if version != 5:
        raise MTEFError(f"不支持 MTEF v{version}")
    r.skip(4)  # 平台、产品、版本、子版本
    r.cstr()  # 应用标识
    r.u8()  # 公式选项（行内 / 独立）
    out: list = []
    while r.peek() is not None:
        tag = r.u8()
        if tag == END:
            break
        obj = _object(r, tag)
        if obj is not None:
            out.append(obj)
    return out


# ---------------- 字符映射 ----------------

GREEK = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon", "ϵ": r"\epsilon",
    "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ϖ": r"\varpi", "ρ": r"\rho",
    "ϱ": r"\varrho", "σ": r"\sigma", "ς": r"\varsigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\varphi",
    "ϕ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi",
    "Σ": r"\Sigma", "Υ": r"\Upsilon", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}
SYMBOLS = {
    "×": r"\times", "÷": r"\div", "±": r"\pm", "∓": r"\mp", "·": r"\cdot", "⋅": r"\cdot", "∙": r"\cdot",
    "≤": r"\le", "≥": r"\ge", "≦": r"\leqslant", "≧": r"\geqslant", "⩽": r"\leqslant", "⩾": r"\geqslant",
    "≠": r"\ne", "≈": r"\approx", "≡": r"\equiv", "≅": r"\cong", "≌": r"\cong", "∽": r"\backsim", "∼": r"\sim",
    "∝": r"\propto", "∞": r"\infty", "∠": r"\angle", "△": r"\triangle", "▵": r"\triangle", "⊥": r"\perp",
    "∥": r"/\!/", "∕": "/", "°": r"^{\circ}", "∘": r"\circ", "′": "'", "″": "''", "‴": "'''",
    "∈": r"\in", "∉": r"\notin", "∋": r"\ni", "⊂": r"\subset", "⊃": r"\supset", "⊆": r"\subseteq",
    "⊇": r"\supseteq", "⫋": r"\subsetneqq", "⊊": r"\subsetneq", "⊄": r"\not\subset", "⊈": r"\nsubseteq",
    "∪": r"\cup", "∩": r"\cap", "∅": r"\varnothing", "Ø": r"\varnothing", "∁": r"\complement",
    "∀": r"\forall", "∃": r"\exists", "¬": r"\neg", "∧": r"\wedge", "∨": r"\vee",
    "→": r"\to", "←": r"\leftarrow", "↔": r"\leftrightarrow", "⇒": r"\Rightarrow", "⇐": r"\Leftarrow",
    "⇔": r"\Leftrightarrow", "↑": r"\uparrow", "↓": r"\downarrow", "⇌": r"\rightleftharpoons",
    "⟶": r"\longrightarrow", "↦": r"\mapsto",
    "∵": r"\because", "∴": r"\therefore", "…": r"\cdots", "⋯": r"\cdots", "⋮": r"\vdots", "⋱": r"\ddots",
    "∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∬": r"\iint", "∮": r"\oint", "√": r"\surd",
    "∂": r"\partial", "∇": r"\nabla", "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}",
    "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}", "ℏ": r"\hbar", "ℓ": r"\ell", "Å": r"\mathring{A}",
    "⊙": r"\odot", "⊕": r"\oplus", "⊗": r"\otimes", "□": r"\square", "◇": r"\diamond", "☆": r"\star",
    "⌒": r"\frown", "⊿": r"\triangle", "∣": r"\mid", "|": "|", "‖": r"\|", "⟨": r"\langle", "⟩": r"\rangle",
    "〈": r"\langle", "〉": r"\rangle", "−": "-", "–": "-", "—": "-", "∶": ":", "：": ":", "，": ",",
    "（": "(", "）": ")", "＝": "=", "＋": "+", "＜": "<", "＞": ">", "{": r"\{", "}": r"\}",
    "#": r"\#", "%": r"\%", "&": r"\&", "$": r"\$", "_": r"\_", "\\": r"\backslash", "~": r"\sim",
    "^": r"\hat{}", " ": " ", "⁡": "", "⁢": "", "⁣": "", "​": "",
}
# MTExtra 私用区等无对应 Unicode 的常见字符
MTCODE_EXTRA = {
    0xEB00: "", 0xEB01: "", 0xEB02: "", 0xEB03: "", 0xEB04: "", 0xEB05: "", 0xEB08: "",
    0xEF00: "", 0xEF01: r"\,", 0xEF02: r"\:", 0xEF03: r"\;", 0xEF04: r"\quad", 0xEF05: r"\,",
    0xEF06: "", 0xEF07: "", 0xEF08: r"\,", 0xEB1A: r"\angle", 0xEC01: "", 0xEC02: "",
    0xE98F: r"\overset{\frown}{}", 0xE90C: r"\cdots", 0xEB0B: r"\perp",
}
FUNCTIONS = {"sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh",
             "log", "ln", "lg", "exp", "lim", "max", "min", "sup", "inf", "det", "deg", "dim", "gcd", "arg"}

EMBELL_TPL = {
    2: r"\dot{%s}", 3: r"\ddot{%s}", 4: r"\dddot{%s}", 5: "%s'", 6: "%s''", 7: "'%s", 8: r"\tilde{%s}",
    9: r"\hat{%s}", 10: r"\not{%s}", 11: r"\vec{%s}", 12: r"\overleftarrow{%s}", 13: r"\overleftrightarrow{%s}",
    14: r"\vec{%s}", 15: r"\overleftarrow{%s}", 16: r"\not{%s}", 17: r"\overline{%s}", 18: "%s'''",
    19: r"\overset{\frown}{%s}", 20: r"\overset{\smile}{%s}", 24: r"\ddddot{%s}", 25: r"\underset{\cdot}{%s}",
    29: r"\underline{%s}", 30: r"\utilde{%s}", 33: r"\underrightarrow{%s}", 34: r"\underleftarrow{%s}",
}

FENCES = {
    0: (r"\langle", r"\rangle"), 1: ("(", ")"), 2: (r"\{", r"\}"), 3: ("[", "]"), 4: ("|", "|"),
    5: (r"\|", r"\|"), 6: (r"\lfloor", r"\rfloor"), 7: (r"\lceil", r"\rceil"), 8: ("[", "["),
}
BIG_OPS = {15: r"\int", 16: r"\sum", 17: r"\prod", 18: r"\coprod", 19: r"\bigcup", 20: r"\bigcap",
           21: r"\int", 22: r"\sum"}


def _is_cjk(c: str) -> bool:
    return any(lo <= ord(c) <= hi for lo, hi in ((0x3000, 0x303F), (0x3400, 0x9FFF), (0xFF00, 0xFFEF)))


class _Emitter:
    def __init__(self) -> None:
        self.unknown: set[int] = set()

    def char(self, ch: Char) -> str:
        code = ch.code
        if ch.typeface == FN_SPACE or code in MTCODE_EXTRA:
            s = MTCODE_EXTRA.get(code, "")
        else:
            try:
                c = chr(code)
            except ValueError:
                self.unknown.add(code)
                return ""
            if 0xE000 <= code <= 0xF8FF:
                self.unknown.add(code)
                return ""
            if c in GREEK:
                s = GREEK[c] + " "
            elif c in SYMBOLS:
                s = SYMBOLS[c]
                if s.startswith("\\") and s[-1].isalpha():
                    s += " "
            elif _is_cjk(c):
                s = rf"\text{{{c}}}"
            else:
                s = c
        if s.startswith("\\") and s[-1:].isalpha():
            s += " "
        for e in ch.embells:
            tpl = EMBELL_TPL.get(e)
            if tpl:
                s = tpl % s
        return s

    def line(self, line: Line | None) -> str:
        if line is None or line.null:
            return ""
        out: list[str] = []
        items = line.items
        i = 0
        while i < len(items):
            it = items[i]
            if isinstance(it, Char):
                # 连续的函数名字符（sin、log…）与正文字符合并输出
                if it.typeface in (FN_FUNCTION, FN_TEXT) and not it.embells:
                    j = i
                    word = []
                    while j < len(items) and isinstance(items[j], Char) and items[j].typeface == it.typeface \
                            and not items[j].embells and (j == i or not items[j].func_start):
                        word.append(items[j])
                        j += 1
                    text = "".join(chr(c.code) if c.code < 0xE000 or c.code > 0xF8FF else "" for c in word)
                    if it.typeface == FN_FUNCTION and text.isascii() and text.isalpha():
                        out.append(f"\\{text} " if text in FUNCTIONS else rf"\operatorname{{{text}}}")
                    elif text.strip() and any(_is_cjk(c) or c.isalpha() for c in text) and len(text) > 1:
                        esc = text.replace("\\", r"\backslash ").replace("{", r"\{").replace("}", r"\}")
                        out.append(rf"\text{{{esc}}}")
                    else:
                        out.append("".join(self.char(c) for c in word))
                    i = j
                    continue
                out.append(self.char(it))
            elif isinstance(it, Tmpl):
                s = self.tmpl(it, out)
                if it.selector in (27, 28, 29) and out and out[-1].startswith(("_", "^", "{}_", "{}^")):
                    s = "{}" + s
                out.append(s)
            elif isinstance(it, Pile):
                out.append(self.pile(it))
            elif isinstance(it, Matrix):
                out.append(self.matrix(it))
            i += 1
        return "".join(out)

    def node(self, n) -> str:  # noqa: ANN001
        if isinstance(n, Line):
            return self.line(n)
        if isinstance(n, Pile):
            return self.pile(n)
        if isinstance(n, Matrix):
            return self.matrix(n)
        if isinstance(n, Char):
            return self.char(n)
        if isinstance(n, Tmpl):
            return self.tmpl(n, [])
        return ""

    def pile(self, p: Pile, env: str = "array") -> str:
        rows = [self.line(x) for x in p.lines]
        if len(rows) == 1:
            return rows[0]
        if env == "cases":
            return r"\begin{cases}" + r" \\ ".join(rows) + r"\end{cases}"
        align = {1: "l", 2: "c", 3: "r"}.get(p.halign, "l")
        return rf"\begin{{array}}{{{align}}}" + r" \\ ".join(rows) + r"\end{array}"

    def matrix(self, m: Matrix) -> str:
        cells = [self.line(x) for x in m.cells]
        rows = [" & ".join(cells[r * m.cols:(r + 1) * m.cols]) for r in range(m.rows)]
        return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"

    def tmpl(self, t: Tmpl, prev: list[str]) -> str:
        sel, var = t.selector, t.variation
        slots = [s for s in t.slots if not isinstance(s, Char)]
        chars = [s for s in t.slots if isinstance(s, Char)]

        def slot(i: int) -> str:
            return self.node(slots[i]) if i < len(slots) else ""

        if sel <= 8:  # 括号
            lo, ro = FENCES[sel]
            left, right = var & 0x1, var & 0x2
            main = slots[0] if slots else None
            if sel == 2 and left and not right and isinstance(main, Line) and len(main.items) == 1 \
                    and isinstance(main.items[0], Pile):
                return self.pile(main.items[0], "cases")
            if sel == 2 and left and not right and isinstance(main, Pile):
                return self.pile(main, "cases")
            inner = slot(0)
            return (rf"\left{lo}" if left else r"\left.") + inner + (rf"\right{ro}" if right else r"\right.")
        if sel == 9:  # 区间
            lmap = {0: "(", 1: ")", 2: "[", 3: "]"}
            return r"\left" + lmap[var & 0x3] + slot(0) + r"\right" + lmap[(var >> 4) & 0x3]
        if sel == 10:  # 根号
            return rf"\sqrt[{slot(1)}]{{{slot(0)}}}" if var & 1 and slot(1) else rf"\sqrt{{{slot(0)}}}"
        if sel == 11:  # 分数
            if var & 0x2:
                return f"{{{slot(0)}}}/{{{slot(1)}}}"
            return rf"\frac{{{slot(0)}}}{{{slot(1)}}}"
        if sel == 12:
            return rf"\underline{{{slot(0)}}}"
        if sel == 13:
            return rf"\overline{{{slot(0)}}}"
        if sel == 14:  # 带文字的箭头
            arrow = r"\xleftarrow" if var & 0x10 else r"\xrightarrow"
            top, bottom = slot(0), slot(1)
            return f"{arrow}[{bottom}]{{{top}}}" if bottom else f"{arrow}{{{top}}}"
        if sel in BIG_OPS:  # 积分、求和等：主体、下限、上限
            op = BIG_OPS[sel]
            if chars:
                c = self.char(chars[-1]).strip()
                op = c if c.startswith("\\") else op
            lower, upper = slot(1), slot(2)
            s = op + (f"_{{{lower}}}" if lower else "") + (f"^{{{upper}}}" if upper else "")
            return s + " " + slot(0)
        if sel == 23:  # 极限：主体、下、上
            main, lower, upper = slot(0), slot(1), slot(2)
            base = main.strip() or r"\lim"
            if base in ("lim", r"\lim", r"\lim "):
                base = r"\lim"
            return base + (f"_{{{lower}}}" if lower else "") + (f"^{{{upper}}}" if upper else "")
        if sel in (24, 25):  # 水平大括号 / 方括号
            top = var & 1
            cmd = (r"\overbrace" if top else r"\underbrace") if sel == 24 else (r"\overline" if top else r"\underline")
            s = f"{cmd}{{{slot(0)}}}"
            return s + ((f"^{{{slot(1)}}}" if top else f"_{{{slot(1)}}}") if slot(1) else "")
        if sel == 26:  # 长除法
            return rf"{slot(1)}\overline{{\left){slot(0)}\right.}}"
        if sel in (27, 28, 29):  # 上下标，作用于前一个元素
            sub, sup = (slot(0), slot(1))
            if sel == 28:
                sub, sup = "", slot(1) or slot(0)
            base = "" if prev else "{}"
            s = base + (f"_{{{sub}}}" if sub else "") + (f"^{{{sup}}}" if sup else "")
            if var & 1:  # 前置上下标
                return "{}" + s
            return s
        if sel == 30:
            return rf"\left\langle {slot(0)} \middle| {slot(1)} \right\rangle"
        if sel == 31:  # 向量箭头
            if var & 0x4:
                return rf"\underrightarrow{{{slot(0)}}}"
            if var & 0x1 and not var & 0x2:
                return rf"\overleftarrow{{{slot(0)}}}"
            if var & 0x1 and var & 0x2:
                return rf"\overleftrightarrow{{{slot(0)}}}"
            return rf"\overrightarrow{{{slot(0)}}}"
        if sel == 32:
            return rf"\widetilde{{{slot(0)}}}"
        if sel == 33:
            return rf"\widehat{{{slot(0)}}}"
        if sel == 34:
            return rf"\overset{{\frown}}{{{slot(0)}}}"
        if sel == 36:
            return rf"\cancel{{{slot(0)}}}"
        if sel == 37:
            return rf"\boxed{{{slot(0)}}}"
        return "".join(self.node(s) for s in slots)


def to_latex(mtef: bytes) -> str:
    objs = parse(mtef)
    em = _Emitter()
    parts = [em.node(o) for o in objs]
    return _tidy("".join(parts))


def _tidy(s: str) -> str:
    import re

    s = re.sub(r"[ \t]+", " ", s).strip()
    s = re.sub(r" ([_^}),.=+\-])", r"\1", s)
    return s


# ---------------- 从 OLE / WMF 中取出 MTEF ----------------

def from_ole(data: bytes) -> bytes:
    """OLE 复合文档中的「Equation Native」流：28 字节 EQNOLEFILEHDR 之后为 MTEF。"""
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(data))
    if not ole.exists("Equation Native"):
        raise MTEFError("OLE 对象中没有 Equation Native")
    native = ole.openstream("Equation Native").read()
    hdr = struct.unpack_from("<H", native)[0]
    return native[hdr:]


# MTEF v5 开头的标准字节（版本、平台、产品、DSMT7、编码定义 WinAllBasicCodePages），
# 部分题库工具在 WMF 中用自己的标识覆盖了这一段，按缺失长度补回
_MTEF_PREFIX = b"\x05\x01\x00\x07\x04DSMT7\x00\x00\x13WinAllBasicCodePages\x00"


def from_wmf(data: bytes) -> bytes:
    """WMF 中的 MathType 数据位于 META_ESCAPE / MFCOMMENT 记录：
    「AppsMFCC」+ 2 字节标志 + 4 字节 MTEF 总长 + 4 字节标识串长度 + 标识串 + 数据。
    同一文件中另有 MathType 写入的 MathML 译文记录（数据以 <?xml 开头），跳过。"""
    sig = b"AppsMFCC"
    out = bytearray()
    total = None
    pos = 0
    while (i := data.find(sig, pos)) >= 0:
        size = struct.unpack_from("<H", data, i - 2)[0]  # 转义记录的数据字节数
        rec = data[i:i + size]
        pos = i + max(size, len(sig))
        if len(rec) < 18:
            continue
        _flags, tot, id_len = struct.unpack_from("<HII", rec, len(sig))
        body = rec[18 + id_len:] if 18 + id_len <= len(rec) else b""
        if rec[18:18 + 14] == b"Design Science" and b"<?xml" in rec[18:18 + id_len + 40]:
            continue
        if total is None:
            total = tot
        out += body
    if not out or total is None:
        raise MTEFError("WMF 中没有 MathType 数据")
    blob = bytes(out[:total])
    missing = total - len(blob) if len(blob) < total else 0
    if blob[:1] != b"\x05":
        missing = missing or next((k for k in range(1, len(_MTEF_PREFIX)) if blob.startswith(_MTEF_PREFIX[k:k + 8])), 0)
        if missing and blob.startswith(_MTEF_PREFIX[missing:missing + 8]):
            blob = _MTEF_PREFIX[:missing] + blob
    return blob
