import io
import json
import zipfile

import httpx
import pytest
import respx

from app.config import Settings
from app.pipeline.files import images_to_pdf, normalize, pdf_page_count, validate_kinds
from app.pipeline.parsers.base import ParserFailed, ParserUnavailable, SourceFile
from app.pipeline.parsers.lite import LiteParser
from app.pipeline.parsers.mineru_cloud import MinerUCloudParser, unescape_markdown
from app.pipeline.parsers.router import AllParsersFailed, parse_with_fallback

from .fixtures import make_exam_pdf, make_scanned_pdf

BASE = "https://mineru.test"
UPLOAD = "https://oss.test/upload/abc?sig=1"
ZIP = "https://cdn.test/result.zip"

CONTENT_LIST = [
    {"type": "header", "text": "绝密★启用前", "page_idx": 0, "bbox": [0, 0, 1000, 30]},
    {"type": "text", "text": "北京市海淀区 2026—2027 学年高一上学期期中考试", "text_level": 1, "page_idx": 0,
     "bbox": [100, 40, 900, 70]},
    {"type": "text", "text": "1．已知 $x^2 = 4$，则 x = （ ）", "page_idx": 0, "bbox": [80, 100, 920, 130]},
    {"type": "equation", "text": "$$\\frac{1}{x}+\\frac{1}{y}$$", "text_format": "latex", "page_idx": 0,
     "bbox": [300, 140, 700, 180]},
    {"type": "image", "img_path": "images/fig1.jpg", "image_caption": ["图 1"], "page_idx": 1,
     "bbox": [100, 200, 500, 500]},
    {"type": "table", "table_body": "<table><tr><td>x</td></tr></table>", "img_path": "images/t1.jpg",
     "page_idx": 1, "bbox": [100, 520, 900, 600]},
    {"type": "page_number", "text": "1", "page_idx": 0, "bbox": [480, 970, 520, 990]},
]


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("exam/exam_content_list.json", json.dumps(CONTENT_LIST, ensure_ascii=False))
        zf.writestr("exam/exam_content_list_v2.json", "[]")
        zf.writestr("exam/full.md", "# 试卷")
        zf.writestr("exam/images/fig1.jpg", b"JPEGDATA")
        zf.writestr("exam/images/t1.jpg", b"TABLEIMG")
    return buf.getvalue()


def _settings(**kw) -> Settings:  # noqa: ANN003
    return Settings(mineru_token="tok", mineru_base_url=BASE, mineru_poll_interval=0, **kw)


@respx.mock
async def test_mineru_cloud_full_flow():
    batch = respx.post(f"{BASE}/api/v4/file-urls/batch").respond(
        json={"code": 0, "data": {"batch_id": "b1", "file_urls": [UPLOAD]}})
    put = respx.put(UPLOAD).respond(200)
    respx.get(f"{BASE}/api/v4/extract-results/batch/b1").mock(side_effect=[
        httpx.Response(200, json={"code": 0, "data": {"extract_result": [{"state": "waiting-file"}]}}),
        httpx.Response(200, json={"code": 0, "data": {"extract_result": [
            {"state": "running", "extract_progress": {"extracted_pages": 1, "total_pages": 2}}]}}),
        httpx.Response(200, json={"code": 0, "data": {"extract_result": [{"state": "done", "full_zip_url": ZIP}]}}),
    ])
    respx.get(ZIP).respond(content=_zip())
    progress: list[float] = []

    async def on_progress(f: float) -> None:
        progress.append(f)

    pdf = make_exam_pdf()
    doc = await MinerUCloudParser(_settings()).parse(SourceFile("exam.pdf", pdf, "pdf"), ocr=True,
                                                     on_progress=on_progress)

    body = json.loads(batch.calls[0].request.content)
    assert batch.calls[0].request.headers["Authorization"] == "Bearer tok"
    assert body["files"][0]["is_ocr"] is True and body["model_version"] == "vlm"
    assert put.calls[0].request.content == pdf
    assert "content-type" not in put.calls[0].request.headers
    assert progress == [0.5]

    # 页眉、页码被过滤；bbox 从 0–1000 归一化到 0–1；page_idx 从 0 转为从 1
    assert [b.type for b in doc.blocks] == ["title", "text", "equation", "image", "table"]
    assert doc.blocks[1].bbox == (0.08, 0.1, 0.92, 0.13)
    assert doc.blocks[3].page == 2 and doc.blocks[3].image == b"JPEGDATA" and doc.blocks[3].content == "图 1"
    assert doc.blocks[4].image == b"TABLEIMG" and doc.blocks[4].content.startswith("<table>")
    assert doc.page_count == 2 and doc.pdf == pdf


@respx.mock
async def test_mineru_cloud_reports_failure():
    respx.post(f"{BASE}/api/v4/file-urls/batch").respond(json={"code": 0, "data": {"batch_id": "b2", "file_urls": [UPLOAD]}})
    respx.put(UPLOAD).respond(200)
    respx.get(f"{BASE}/api/v4/extract-results/batch/b2").respond(
        json={"code": 0, "data": {"extract_result": [{"state": "failed", "err_msg": "文件损坏"}]}})
    with pytest.raises(ParserFailed, match="文件损坏"):
        await MinerUCloudParser(_settings()).parse(SourceFile("a.pdf", b"%PDF", "pdf"), ocr=False)


@respx.mock
async def test_mineru_cloud_api_error_code():
    respx.post(f"{BASE}/api/v4/file-urls/batch").respond(json={"code": -60005, "msg": "额度不足"})
    with pytest.raises(ParserFailed, match="额度不足"):
        await MinerUCloudParser(_settings()).parse(SourceFile("a.pdf", b"%PDF", "pdf"), ocr=False)


async def test_mineru_cloud_without_token_is_unavailable():
    with pytest.raises(ParserUnavailable):
        await MinerUCloudParser(Settings(mineru_token="")).parse(SourceFile("a.pdf", b"", "pdf"), ocr=False)


async def test_lite_rejects_scanned_pdf_and_docx():
    with pytest.raises(ParserUnavailable, match="扫描件"):
        await LiteParser().parse(SourceFile("s.pdf", make_scanned_pdf(), "pdf"), ocr=False)
    with pytest.raises(ParserUnavailable):
        await LiteParser().parse(SourceFile("a.docx", b"PK", "docx"), ocr=False)


@respx.mock
async def test_router_falls_back_from_cloud_to_lite():
    respx.post(f"{BASE}/api/v4/file-urls/batch").mock(side_effect=httpx.ConnectError("boom"))
    s = _settings(parser_chain=["mineru_cloud", "lite"])
    doc, name, warnings = await parse_with_fallback(SourceFile("e.pdf", make_exam_pdf(), "pdf"), ocr=False, settings=s)
    assert name == "lite" and doc.blocks
    assert any("mineru_cloud 解析失败" in w for w in warnings)


async def test_router_all_unavailable():
    s = Settings(mineru_token="", parser_chain=["mineru_cloud", "lite"])
    with pytest.raises(AllParsersFailed, match="扫描件"):
        await parse_with_fallback(SourceFile("s.pdf", make_scanned_pdf(), "pdf"), ocr=True, settings=s)


def test_images_are_merged_into_one_pdf():
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 60), False)
    png = pix.tobytes("png")
    src = normalize([("p1.png", png), ("p2.png", png), ("p3.png", png)])
    assert src.kind == "pdf" and src.name == "p1.pdf" and pdf_page_count(src.data) == 3
    assert pdf_page_count(images_to_pdf([("a.png", png)])) == 1


@pytest.mark.parametrize(("names", "ok"), [
    (["a.pdf"], "pdf"), (["a.docx"], "docx"), (["a.jpg", "b.PNG"], "image"),
    (["a.pdf", "b.png"], None), (["a.txt"], None),
])
def test_validate_kinds(names, ok):
    if ok:
        assert validate_kinds(names) == ok
    else:
        with pytest.raises(ValueError):
            validate_kinds(names)


@respx.mock
async def test_mineru_zip_download_failure_is_explained():
    respx.post(f"{BASE}/api/v4/file-urls/batch").respond(json={"code": 0, "data": {"batch_id": "b3", "file_urls": [UPLOAD]}})
    respx.put(UPLOAD).respond(200)
    respx.get(f"{BASE}/api/v4/extract-results/batch/b3").respond(
        json={"code": 0, "data": {"extract_result": [{"state": "done", "full_zip_url": ZIP}]}})
    respx.get(ZIP).mock(side_effect=httpx.ConnectError(""))
    with pytest.raises(ParserFailed, match=r"下载结果失败（cdn.test）：ConnectError"):
        await MinerUCloudParser(_settings()).parse(SourceFile("a.pdf", b"%PDF", "pdf"), ocr=False)


def test_lite_drops_page_furniture():
    from app.pipeline.ir import Block
    from app.pipeline.parsers.lite import drop_page_furniture
    rows = []
    for p in (1, 2, 3):
        rows += [(p, f"试卷第{p}页（共3页）"), (p, f"{p}．第 {p} 题的题干"), (p, "福格教研部")]
    rows.append((3, "- 3 -"))
    blocks = [Block(i, p, (0, 0, 1, 1), "text", t) for i, (p, t) in enumerate(rows, 1)]
    kept = drop_page_furniture(blocks, 3)
    assert [b.text for b in kept] == ["1．第 1 题的题干", "2．第 2 题的题干", "3．第 3 题的题干"]
    assert [b.seq for b in kept] == [1, 2, 3]


def test_lite_scripts_and_linear_math():
    from app.pipeline.parsers.lite import line_text, linear_math

    def span(t, size, y, x0, x1):
        return {"text": t, "size": size, "origin": (x0, y), "bbox": (x0, y - size, x1, y)}

    # 化学式：小字号、基线下移 → 下标；离子电荷基线上移 → 上标
    line = [span("生成", 10.4, 601.8, 0, 20), span("P", 10.4, 601.8, 20, 27), span("2", 6.8, 602.7, 27, 31),
            span("O", 10.4, 601.8, 31, 38), span("5", 6.8, 602.7, 38, 42), span(" 与 SO", 10.4, 602.7, 42, 70),
            span("4", 6.8, 602.7, 70, 74), span("2-", 6.8, 597.5, 74, 80)]
    assert line_text(line) == "生成P₂O₅ 与 SO₄²⁻"
    # 没有 Unicode 字符的上标并入 LaTeX；与前文有空隙的小字不当作上下标
    assert line_text([span("x", 10.5, 100, 0, 6), span("k+m", 7, 96, 6, 16)]) == "$x^{k+m}$"
    assert line_text([span("得分", 10.5, 100, 0, 20), span("12", 7, 100, 40, 48)]) == "得分12"

    assert linear_math("B = { x | x^2 ≤ 1 }") == "B = { x | x² ≤ 1 }"
    assert linear_math("a_{n+1} = 2^(n+1)，a_n") == "aₙ₊₁ = 2ⁿ⁺¹，aₙ"
    assert linear_math("f(x) = √(x - 1)，e^x") == "f(x) = $\\sqrt{x - 1}$，$e^{x}$"
    # 填空横线、已含 LaTeX 的文字不处理
    assert linear_math("____ 与 A_B") == "____ 与 A_B"
    assert linear_math("已知 $x^2 = 4$") == "已知 $x^2 = 4$"


def test_mineru_markdown_escapes_restored_outside_math():
    assert unescape_markdown(r"则底边长为 \_\_\_\_ cm") == "则底边长为 ____ cm"
    assert unescape_markdown(r"\* 号 $a\_b$ 与 $$x\_1$$") == r"* 号 $a\_b$ 与 $$x\_1$$"
