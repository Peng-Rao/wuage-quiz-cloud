"""Word 本地解析：MathType（MTEF）/ OMML 公式转 LaTeX、文档结构还原，以及无法识别时改用 MinerU。"""

import io
import struct
import zipfile

import pymupdf
import pytest

from app.config import Settings
from app.pipeline.ir import Block, ParsedDocument
from app.pipeline.parsers import mtef
from app.pipeline.parsers.base import ParserFailed, ParserUnavailable, SourceFile
from app.pipeline.parsers.docx_native import DocxParser, format_number, parse_docx
from app.pipeline.parsers.router import parse_with_fallback

# ---------------- MTEF ----------------

HDR = b"\x05\x01\x00\x07\x00DSMT7\x00\x00"


def ch(c: str, typeface: int = 3) -> bytes:
    return b"\x02\x00" + bytes([typeface + 128]) + struct.pack("<H", ord(c))


def line(*items: bytes) -> bytes:
    return b"\x01\x00" + b"".join(items) + b"\x00"


NULL_LINE = b"\x01\x01"


def tmpl(selector: int, variation: int, *slots: bytes) -> bytes:
    return b"\x03\x00" + bytes([selector, variation, 0]) + b"".join(slots) + b"\x00"


def test_mtef_script_fraction_and_symbols():
    # x² + ½ ≤ π
    data = HDR + line(
        ch("x"), tmpl(28, 0, NULL_LINE, line(ch("2", 8))), ch("+", 6),
        tmpl(11, 0, line(ch("1", 8)), line(ch("2", 8))), ch("≤", 6), ch("π", 4),
    ) + b"\x00"
    assert mtef.to_latex(data) == r"x^{2}+\frac{1}{2}\le \pi"


def test_mtef_fence_cases_and_line_ruler():
    # 左大括号 + 两行堆叠 → cases；带制表位的 LINE（制表位不带记录类型字节）
    pile = b"\x04\x00\x01\x00" + line(ch("x"), ch(">", 6), ch("0", 8)) + line(ch("y"), ch("<", 6), ch("1", 8)) + b"\x00"
    ruler_line = b"\x01\x02" + b"\x01\x00\x44\x13" + tmpl(2, 1, line(pile)) + b"\x00"
    assert mtef.to_latex(HDR + ruler_line + b"\x00") == r"\begin{cases}x>0 \\ y<1\end{cases}"


def test_mtef_embellishment_and_function():
    # A′、sin x（函数字体连续字符合并）
    prime = b"\x02\x01\x83" + struct.pack("<H", ord("A")) + b"\x06\x00\x05\x00"
    data = HDR + line(prime, ch("s", 2), ch("i", 2), ch("n", 2), ch("x")) + b"\x00"
    assert mtef.to_latex(data) == r"A'\sin x"


def wmf_record(ident: bytes, body: bytes, total: int) -> bytes:
    payload = b"AppsMFCC" + struct.pack("<HII", 1, total, len(ident)) + ident + body
    return b"\x26\x06\x0f\x00" + struct.pack("<H", len(payload)) + payload


def test_mtef_from_wmf_including_overwritten_prefix():
    m = HDR + line(ch("a")) + b"\x00"
    normal = b"WMFHEAD" + wmf_record(b"Design Science, Inc.\x00", m, len(m))
    assert mtef.to_latex(mtef.from_wmf(normal)) == "a"
    # 题库工具用自己的标识覆盖了 MTEF 开头 23 字节
    full = mtef._MTEF_PREFIX + line(ch("b")) + b"\x00"
    tampered = wmf_record(b"Apps-QBM123,2024-10-17", full[23:], len(full))
    assert mtef.to_latex(mtef.from_wmf(tampered)) == "b"
    with pytest.raises(mtef.MTEFError):
        mtef.from_wmf(b"no mathtype here")


def test_format_number():
    assert [format_number(3, f) for f in ("decimal", "upperLetter", "lowerRoman", "chineseCounting",
                                          "decimalEnclosedCircle")] == ["3", "C", "iii", "三", "③"]
    assert format_number(12, "chineseCounting") == "十二"


# ---------------- docx 结构 ----------------

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"')
RELS = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="media/image1.png"/>'
        '<Relationship Id="rId2" Target="embeddings/oleObject1.bin"/>'
        '<Relationship Id="rId3" Target="media/image2.wmf"/></Relationships>')
NUMBERING = (f'<w:numbering {NS}><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
             '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
             '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')


def p(*runs: str, num: bool = False) -> str:
    ppr = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>' if num else ""
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"


def r(text: str, rpr: str = "") -> str:
    return f'<w:r>{f"<w:rPr>{rpr}</w:rPr>" if rpr else ""}<w:t xml:space="preserve">{text}</w:t></w:r>'


def png() -> bytes:
    return pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False).tobytes("png")


def make_docx(body: str, files: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {NS}><w:body>{body}</w:body></w:document>')
        z.writestr("word/_rels/document.xml.rels", RELS)
        z.writestr("word/numbering.xml", NUMBERING)
        z.writestr("word/media/image1.png", png())
        for k, v in (files or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


OLE = ('<w:r><w:object><v:shape><v:imagedata r:id="rId3"/></v:shape>'
       '<o:OLEObject ProgID="Equation.DSMT4" r:id="rId2"/></w:object></w:r>')
DRAWING = ('<w:r><w:drawing><wp:inline><wp:extent cx="1270000" cy="1270000"/><a:graphic><a:graphicData>'
           '<a:blip r:embed="rId1"/></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')
OMML_FRAC = '<m:oMath><m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den><m:r><m:t>3</m:t></m:r></m:den></m:f></m:oMath>'
LONG = "本题考查有理数的运算，计算时注意运算顺序与符号，先算乘方再算乘除最后算加减，结果要化为最简形式。"


def test_docx_structure(monkeypatch):
    m = HDR + line(ch("x"), tmpl(28, 0, NULL_LINE, line(ch("2", 8)))) + b"\x00"
    monkeypatch.setattr(mtef, "from_ole", lambda data: m)
    body = "".join([
        p(r("一、选择题"), num=False),
        p(r("计算 "), OLE, r("，结果为"), r("   ", '<w:u w:val="single"/>'), r("。"), num=True),
        p(r("面积单位 cm"), r("2", '<w:vertAlign w:val="superscript"/>'), r("隐藏", "<w:vanish/>"), num=True),
        p(r("分数 "), OMML_FRAC),
        p(r("看图："), DRAWING, r("回答")),
        '<w:tbl><w:tr><w:tc><w:p>' + r("A. 1") + '</w:p></w:tc><w:tc><w:p>' + r("B. 2") + "</w:p></w:tc></w:tr></w:tbl>",
        '<w:tbl><w:tr><w:tc><w:p>' + r("题号") + '</w:p></w:tc><w:tc><w:p>' + r("1") + "</w:p></w:tc></w:tr>"
        '<w:tr><w:tc><w:p>' + r("答案") + '</w:p></w:tc><w:tc><w:p>' + r("x&lt;2") + "</w:p></w:tc></w:tr></w:tbl>",
        p(r(LONG)),
    ])
    doc = parse_docx(make_docx(body, {"word/embeddings/oleObject1.bin": b"ole"}))
    got = [(b.type, b.content) for b in doc.blocks]
    assert got[:8] == [
        ("text", "一、选择题"),
        ("text", "1. 计算 $x^{2}$，结果为____。"),
        ("text", "2. 面积单位 cm$^{2}$"),
        ("text", r"分数 $\frac{1}{3}$"),
        ("text", "看图："),
        ("image", ""),
        ("text", "回答"),
        ("text", "A. 1 B. 2"),
    ]
    assert got[8] == ("table", "<table><tr><td>题号</td><td>1</td></tr><tr><td>答案</td><td>x&lt;2</td></tr></table>")
    assert doc.blocks[5].image and doc.blocks[5].image_ext == "png"
    assert doc.pdf is None and doc.page_count == 1 and not doc.warnings


def test_docx_formula_failures(monkeypatch):
    def broken(data: bytes) -> bytes:
        raise mtef.MTEFError("坏数据")

    monkeypatch.setattr(mtef, "from_ole", broken)
    files = {"word/embeddings/oleObject1.bin": b"ole", "word/media/image2.wmf": b"not mathtype"}
    one = parse_docx(make_docx(p(r("算式 "), OLE) + p(r(LONG)), files))
    assert one.blocks[0].content == "算式 [公式]" and "1 个公式无法转换" in one.warnings[0]
    # 失败过多：放弃本地解析，交给下一个引擎
    with pytest.raises(ParserFailed, match="公式无法转换"):
        parse_docx(make_docx("".join(p(r(f"{i}. "), OLE) for i in range(4)) + p(r(LONG)), files))


def test_docx_image_only_is_unavailable():
    with pytest.raises(ParserUnavailable, match="扫描件"):
        parse_docx(make_docx(p(DRAWING) + p(DRAWING)))


class FakeMinerU:
    name = "mineru_cloud"

    async def parse(self, src, *, ocr, on_progress=None):  # noqa: ANN001, ARG002
        return ParsedDocument([Block(1, 1, (0, 0, 1, 1), "text", "1. MinerU 识别")], 1)


async def test_unrecognizable_docx_falls_back_to_mineru():
    s = Settings(docx_parser_chain=["docx", "mineru_cloud"])
    for data in (make_docx(p(DRAWING)),                                  # 扫描件
                 b"PK not a zip",                                         # 文件损坏
                 make_docx("<w:p><w:r><w:t>unclosed</w:r></w:p>")):       # 文档结构异常
        doc, name, warnings = await parse_with_fallback(SourceFile("a.docx", data, "docx"), ocr=True, settings=s,
                                                        parsers=[DocxParser(), FakeMinerU()])
        assert name == "mineru_cloud" and doc.blocks[0].content == "1. MinerU 识别"
        assert warnings and "docx" in warnings[-1]


async def test_docx_chain_used_only_for_docx():
    s = Settings(docx_parser_chain=["docx"], parser_chain=["lite"])
    doc, name, _ = await parse_with_fallback(SourceFile("a.docx", make_docx(p(r(LONG))), "docx"), ocr=True, settings=s)
    assert name == "docx" and doc.blocks[0].content == LONG


# ---------------- 解析任务：本地解析识别不出题目时改用 MinerU ----------------

from .fixtures import make_exam_pdf  # noqa: E402
from .test_api import client, upload, wait_done  # noqa: E402, F401


def test_job_retries_with_mineru_when_no_questions(client, monkeypatch):  # noqa: F811
    from app.pipeline import run
    from app.pipeline.parsers.lite import _parse_pdf

    class ExamMinerU:
        name = "mineru_cloud"

        async def parse(self, src, *, ocr, on_progress=None):  # noqa: ANN001, ARG002
            return _parse_pdf(make_exam_pdf())

    real = run.build_parser
    monkeypatch.setattr(run, "build_parser", lambda n, s: ExamMinerU() if n == "mineru_cloud" else real(n, s))
    monkeypatch.setattr("app.pipeline.parsers.router.build_parser",
                        lambda n, s: ExamMinerU() if n == "mineru_cloud" else real(n, s))
    data = make_docx(p(r(LONG)) + p(r(LONG)))  # 文字正常，但没有题号
    key = upload(client, "说明.docx", data)
    job = client.post("/api/parse-jobs", json={"fileKeys": [key], "fileNames": ["说明.docx"],
                                               "options": {"knowledge": False, "dedupe": False}}).json()
    job = wait_done(client, job["id"])
    assert job["status"] == "done", job.get("error")
    assert job["parser"] == "mineru_cloud"
    from app.db import ParseJob, SessionLocal

    with SessionLocal() as s:
        warnings = s.get(ParseJob, job["id"]).warnings
    assert "本地 Word 解析未识别到题目，已改用 MinerU 识别" in warnings
    assert "解析结果不含原始 PDF，无法查看原图" not in warnings
    qs = client.get(f"/api/parse-jobs/{job['id']}/questions").json()
    assert len(qs) >= 4
