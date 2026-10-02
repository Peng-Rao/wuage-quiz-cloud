"""Word 原生公式（OMML，m:oMath）→ LaTeX。覆盖试卷中常见的结构。"""

from xml.etree.ElementTree import Element

from .mtef import GREEK, SYMBOLS

M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

ACCENTS = {"̇": r"\dot", "̈": r"\ddot", "̂": r"\hat", "̃": r"\tilde", "⃗": r"\vec",
           "̅": r"\overline", "¯": r"\overline", "→": r"\vec", "́": r"\acute", "̀": r"\grave",
           "̌": r"\check", "̆": r"\breve", "⃖": r"\overleftarrow", "⃡": r"\overleftrightarrow",
           "⌒": r"\overset{\frown}"}
NARY = {"∑": r"\sum", "∏": r"\prod", "∐": r"\coprod", "∫": r"\int", "∬": r"\iint", "∭": r"\iiint", "∮": r"\oint",
        "⋃": r"\bigcup", "⋂": r"\bigcap", "⋁": r"\bigvee", "⋀": r"\bigwedge"}
FENCE = {"(": "(", ")": ")", "[": "[", "]": "]", "{": r"\{", "}": r"\}", "|": "|", "‖": r"\|", "⟨": r"\langle",
         "⟩": r"\rangle", "〈": r"\langle", "〉": r"\rangle", "⌊": r"\lfloor", "⌋": r"\rfloor", "⌈": r"\lceil",
         "⌉": r"\rceil", "": "."}


def _val(el: Element | None, tag: str, default: str | None = None) -> str | None:
    """取属性节点 m:xxxPr/m:tag 的 m:val。"""
    if el is None:
        return default
    node = el.find(M + tag)
    if node is None:
        return default
    return node.get(M + "val", default)


def _text(s: str) -> str:
    out = []
    for c in s:
        if c in GREEK:
            out.append(GREEK[c] + " ")
        elif c in SYMBOLS:
            t = SYMBOLS[c]
            out.append(t + " " if t.startswith("\\") and t[-1:].isalpha() else t)
        elif "㐀" <= c <= "鿿" or "　" <= c <= "〿" or "＀" <= c <= "￯":
            out.append(rf"\text{{{c}}}")
        else:
            out.append(c)
    return "".join(out)


def _kids(el: Element | None) -> str:
    if el is None:
        return ""
    return "".join(convert(c) for c in el)


def convert(el: Element) -> str:  # noqa: C901, PLR0911, PLR0912
    tag = el.tag
    if tag in (M + "oMath", M + "e", M + "num", M + "den", M + "sub", M + "sup", M + "deg", M + "lim", M + "fName",
               M + "oMathPara"):
        return _kids(el)
    if tag == M + "r":
        t = "".join(x.text or "" for x in el.iter(M + "t"))
        return _text(t)
    if tag == M + "f":
        num, den = _kids(el.find(M + "num")), _kids(el.find(M + "den"))
        if _val(el.find(M + "fPr"), "type") == "lin":
            return f"{num}/{den}"
        return rf"\frac{{{num}}}{{{den}}}"
    if tag in (M + "sSup", M + "sSub", M + "sSubSup", M + "sPre"):
        base = _kids(el.find(M + "e"))
        sub, sup = el.find(M + "sub"), el.find(M + "sup")
        s = (f"_{{{_kids(sub)}}}" if sub is not None else "") + (f"^{{{_kids(sup)}}}" if sup is not None else "")
        if tag == M + "sPre":
            return "{}" + s + base
        return f"{{{base}}}{s}" if len(base) > 1 and not base.startswith("\\") else base + s
    if tag == M + "rad":
        deg_hide = _val(el.find(M + "radPr"), "degHide") in ("1", "on", "true")
        deg = _kids(el.find(M + "deg"))
        body = _kids(el.find(M + "e"))
        return rf"\sqrt[{deg}]{{{body}}}" if deg and not deg_hide else rf"\sqrt{{{body}}}"
    if tag == M + "d":
        pr = el.find(M + "dPr")
        beg, end, sep = _val(pr, "begChr", "("), _val(pr, "endChr", ")"), _val(pr, "sepChr", "|")
        parts = [_kids(e) for e in el.findall(M + "e")]
        if beg == "{" and end == "" and len(parts) == 1:
            inner = el.find(M + "e")
            arr = inner.find(M + "eqArr") if inner is not None else None
            if arr is not None:
                rows = [_kids(e) for e in arr.findall(M + "e")]
                return r"\begin{cases}" + r" \\ ".join(rows) + r"\end{cases}"
        mid = _text(sep) if sep else ","
        return rf"\left{FENCE.get(beg, beg)}" + mid.join(parts) + rf"\right{FENCE.get(end, end)}"
    if tag == M + "nary":
        pr = el.find(M + "naryPr")
        op = NARY.get(_val(pr, "chr", "∫") or "∫", r"\int")
        sub, sup = el.find(M + "sub"), el.find(M + "sup")
        s = op + (f"_{{{_kids(sub)}}}" if sub is not None and len(sub) else "")
        s += f"^{{{_kids(sup)}}}" if sup is not None and len(sup) else ""
        return s + " " + _kids(el.find(M + "e"))
    if tag == M + "func":
        name = _kids(el.find(M + "fName")).strip()
        if name.isalpha():
            name = "\\" + name + " "
        return name + _kids(el.find(M + "e"))
    if tag == M + "acc":
        cmd = ACCENTS.get(_val(el.find(M + "accPr"), "chr", "̂") or "̂", r"\hat")
        return f"{cmd}{{{_kids(el.find(M + 'e'))}}}"
    if tag == M + "bar":
        pos = _val(el.find(M + "barPr"), "pos", "bot")
        cmd = r"\overline" if pos == "top" else r"\underline"
        return f"{cmd}{{{_kids(el.find(M + 'e'))}}}"
    if tag == M + "groupChr":
        pr = el.find(M + "groupChrPr")
        ch, pos = _val(pr, "chr", "⏟"), _val(pr, "pos", "bot")
        body = _kids(el.find(M + "e"))
        if ch in ("⏞", "⏟"):
            return (r"\overbrace" if ch == "⏞" else r"\underbrace") + f"{{{body}}}"
        return (rf"\overset{{{_text(ch)}}}" if pos == "top" else rf"\underset{{{_text(ch)}}}") + f"{{{body}}}"
    if tag in (M + "limLow", M + "limUpp"):
        base, lim = _kids(el.find(M + "e")), _kids(el.find(M + "lim"))
        if base.strip() in ("lim", r"\lim"):
            base = r"\lim"
        return base + (f"_{{{lim}}}" if tag == M + "limLow" else f"^{{{lim}}}")
    if tag == M + "eqArr":
        rows = [_kids(e) for e in el.findall(M + "e")]
        return r"\begin{array}{l}" + r" \\ ".join(rows) + r"\end{array}"
    if tag == M + "m":
        rows = [" & ".join(_kids(e) for e in mr.findall(M + "e")) for mr in el.findall(M + "mr")]
        return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"
    if tag in (M + "box", M + "borderBox", M + "phant"):
        return _kids(el.find(M + "e"))
    if tag.endswith("Pr") or tag in (M + "ctrlPr",):
        return ""
    if tag == W + "r":  # 公式中混入的普通文字
        return _text("".join(x.text or "" for x in el.iter(W + "t")))
    return _kids(el)


def to_latex(el: Element) -> str:
    import re

    s = convert(el)
    return re.sub(r"[ \t]+", " ", s).strip()
