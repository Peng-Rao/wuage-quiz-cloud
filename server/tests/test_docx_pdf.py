"""本地解析的 Word：在转换出的 PDF 中定位版面单元，以及已有任务的原图区域换算。"""

import io
import zipfile
from xml.sax.saxutils import escape

import pymupdf
import pytest

from app.db import ParseJob, SessionLocal
from app.docx_pages import remap_regions
from app.pipeline import docx_pdf, run
from app.pipeline.docx_pdf import docx_to_pdf, locate_blocks
from app.pipeline.ir import Block
from app.pipeline.parsers.docx_native import parse_docx

from .fixtures import EXAM, make_exam_pdf
from .test_api import client, upload, wait_done  # noqa: F401


def make_pdf(pages: list[list[tuple[float, str | None]]]) -> bytes:
    """每页若干行：(y, 文字)；文字为 None 时在该处画一张图片。"""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    with pymupdf.open() as doc:
        for lines in pages:
            page = doc.new_page(width=600, height=800)
            for y, text in lines:
                if text is None:
                    page.insert_image(pymupdf.Rect(100, y, 300, y + 80), pixmap=pix)
                else:
                    page.insert_text((60, y), text, fontsize=12)
        return doc.tobytes()


def blk(seq: int, content: str, type_: str = "text") -> Block:
    return Block(seq, 1, (0.0, 0.0, 1.0, 1.0), type_, content)  # type: ignore[arg-type]


def test_locate_blocks_by_text_and_images():
    pdf = make_pdf([
        [(80, "1. Which picture is right?"), (100, "A."), (110, None), (220, "B."), (230, None)],
        [(80, "2. What does the boy want?"), (120, "A. A pen"), (140, "B. A book")],
    ])
    blocks = [blk(1, "1. Which picture is right?"), blk(2, "A."), blk(3, "", "image"), blk(4, "B."),
              blk(5, "", "image"), blk(6, "$$x^2$$", "equation"), blk(7, "2．What does the boy want?"),
              blk(8, "A. A pen"), blk(9, "<table><tr><td>B. A book</td></tr></table>", "table")]
    assert locate_blocks(pdf, blocks) == 8
    pages = [b.page for b in blocks]
    assert pages == [1, 1, 1, 1, 1, 1, 2, 2, 2]
    # 文字框贴近所在行（PDF 高 800），图片取实际位置
    assert abs(blocks[0].bbox[3] - 80 / 800) < 0.01
    assert abs(blocks[2].bbox[1] - 110 / 800) < 0.01 and abs(blocks[2].bbox[3] - 190 / 800) < 0.01
    assert abs(blocks[4].bbox[1] - 230 / 800) < 0.01
    assert blocks[7].bbox[1] > blocks[6].bbox[3]
    # 公式在 PDF 中找不到：沿用相邻单元的位置
    assert blocks[5].page == 1


def test_repeated_short_labels_match_in_order():
    pdf = make_pdf([[(100 + 40 * i, t) for i, t in enumerate(["1. A.", "B.", "C.", "2. A.", "B.", "C."])]])
    blocks = [blk(i + 1, t) for i, t in enumerate(["1. A.", "B.", "C.", "2. A.", "B.", "C."])]
    locate_blocks(pdf, blocks)
    ys = [b.bbox[1] for b in blocks]
    assert ys == sorted(ys) and len(set(ys)) == 6


def test_docx_to_pdf_without_libreoffice(monkeypatch):
    monkeypatch.setattr(docx_pdf.shutil, "which", lambda _: None)
    assert docx_pdf.docx_to_pdf(b"PK") is None


def test_remap_regions_uses_blocks_inside_old_region():
    # 旧版面：第 1 页四个单元纵向均分；题目区域覆盖第 2、3 个单元
    old = {"b1": (1, [0, 0, 1, .25]), "b2": (1, [0, .25, 1, .5]), "b3": (1, [0, .5, 1, .75]), "b4": (1, [0, .75, 1, 1])}
    new = {"b1": (1, (.1, .1, .9, .2)), "b2": (1, (.1, .8, .9, .9)), "b3": (2, (.1, .05, .5, .1)),
           "b4": (2, (.1, .2, .9, .3))}
    regions = [{"page": 1, "bbox": [0.0, 0.25, 1.0, 0.75]}]
    assert remap_regions(regions, old, new) == [
        {"page": 1, "bbox": [0.1, 0.8, 0.9, 0.9]}, {"page": 2, "bbox": [0.1, 0.05, 0.5, 0.1]}]
    # 找不到对应单元时保留原区域
    assert remap_regions([{"page": 3, "bbox": [0, 0, 1, 1]}], old, new) == [{"page": 3, "bbox": [0, 0, 1, 1]}]


# ---------------- 解析流水线 ----------------

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def exam_docx() -> bytes:
    """与 make_exam_pdf 内容相同的 Word：None 处插入分页符。"""
    paras = "".join('<w:p><w:r><w:br w:type="page"/></w:r></w:p>' if x is None else
                    f'<w:p><w:r><w:t xml:space="preserve">{escape(x[0])}</w:t></w:r></w:p>' for x in EXAM)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="r1" Target="word/document.xml" Type="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships/officeDocument"/></Relationships>')
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{paras}</w:body></w:document>')
    return buf.getvalue()


def test_docx_job_gets_source_pages(client, monkeypatch):  # noqa: F811
    # 不依赖本机 LibreOffice：转换结果用内容相同的 PDF 代替
    calls = []
    monkeypatch.setattr(run, "docx_to_pdf", lambda data: calls.append(data) or make_exam_pdf())
    data = exam_docx()
    key = upload(client, "海淀期中.docx", data)
    r = client.post("/api/parse-jobs", json={"fileKeys": [key], "fileNames": ["海淀期中.docx"], "options": {}})
    job = wait_done(client, r.json()["id"])
    assert job["status"] == "done", job.get("error")
    assert job["parser"] == "docx" and calls == [data] and job["pageCount"] == 3
    with SessionLocal() as s:
        assert not any("无法查看原图" in w for w in s.get(ParseJob, job["id"]).warnings)
    qs = client.get(f"/api/parse-jobs/{job['id']}/questions").json()
    # 第 6 题在分页符之后：位于 PDF 第 2 页
    assert [q["regions"][0]["page"] for q in qs] == [1, 1, 1, 1, 1, 2, 2, 2, 2]
    src = client.get(f"/api/draft-questions/{qs[5]['id']}/source").json()
    assert [s["page"] for s in src] == [2] and src[0]["regions"][0]["bbox"][1] < 0.2
    assert client.get(src[0]["url"]).headers["content-type"] == "image/png"


@pytest.mark.skipif(docx_pdf.soffice() is None, reason="未安装 LibreOffice")
def test_libreoffice_converts_and_locates():
    data = exam_docx()
    pdf = docx_to_pdf(data)
    assert pdf and pymupdf.open(stream=pdf, filetype="pdf").page_count >= 3
    doc = parse_docx(data)
    locate_blocks(pdf, doc.blocks)
    page_of: dict[str, int] = {}
    for b in doc.blocks:  # 取第一次出现（卷末答案也以题号开头）
        page_of.setdefault(b.content[:2], b.page)
    assert page_of["1．"] == 1 and page_of["6．"] == 2 and page_of["参考"] == 3


def test_backfill_existing_docx_job(client, monkeypatch):  # noqa: F811
    from app import docx_pages
    from app.storage import get_store

    # 旧版本解析：没有 PDF，题目区域按分页符估算、横向占满整页
    monkeypatch.setattr(run, "docx_to_pdf", lambda data: None)
    key = upload(client, "旧卷.docx", exam_docx())
    job = wait_done(client, client.post("/api/parse-jobs", json={
        "fileKeys": [key], "fileNames": ["旧卷.docx"], "options": {}}).json()["id"])
    assert job["status"] == "done" and job["pageCount"] == 3
    qs = client.get(f"/api/parse-jobs/{job['id']}/questions").json()
    assert qs[5]["regions"][0]["bbox"][0] == 0 and qs[5]["regions"][0]["bbox"][2] == 1
    assert client.get(f"/api/draft-questions/{qs[5]['id']}/source").json() == []
    with SessionLocal() as s:
        assert any("无法查看原图" in w for w in s.get(ParseJob, job["id"]).warnings)

    monkeypatch.setattr(docx_pages, "docx_to_pdf", lambda data: make_exam_pdf())
    assert job["id"] in docx_pages.candidates(None, False)
    assert docx_pages.backfill_job(job["id"]).startswith("3 页")
    assert get_store().exists(f"jobs/{job['id']}/pages/3.png")
    assert job["id"] not in docx_pages.candidates(None, False)  # 已有页面图的跳过
    qs = client.get(f"/api/parse-jobs/{job['id']}/questions").json()
    assert [q["regions"][0]["page"] for q in qs] == [1, 1, 1, 1, 1, 2, 2, 2, 2]
    src = client.get(f"/api/draft-questions/{qs[5]['id']}/source").json()
    bbox = src[0]["regions"][0]["bbox"]
    assert [x["page"] for x in src] == [2] and bbox[0] > 0 and bbox[2] < 1 and bbox[1] < 0.2  # 实际页边距内
    with SessionLocal() as s:
        j = s.get(ParseJob, job["id"])
        assert j.page_count == 3 and not any("无法查看原图" in w for w in j.warnings)
