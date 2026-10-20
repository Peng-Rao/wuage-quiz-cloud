"""为本地解析的 Word 试卷补生成原图：python -m app.docx_pages [--job ID ...] [--limit N] [--force]。

逐份把原始 .docx 转成 PDF、渲染页面图，并按文字在 PDF 中的位置更新版面单元、题目的原图区域与页码。
已有页面图的任务跳过（--force 重新生成）。每份独立提交，失败可重跑。
"""
import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .db import BankQuestion, DraftQuestion, ParseBlock, ParseJob, SessionLocal
from .pipeline.docx_pdf import docx_to_pdf, locate_blocks
from .pipeline.files import pdf_page_count, render_pages
from .pipeline.ir import Block
from .pipeline.run import PAGE_BATCH, PUT_CONCURRENCY
from .config import get_settings
from .storage import get_store

log = logging.getLogger(__name__)

NO_PDF = "解析结果不含原始 PDF，无法查看原图"
EPS = 1e-3


def _inside(bbox: list[float], region: list[float]) -> bool:
    return (bbox[0] >= region[0] - EPS and bbox[1] >= region[1] - EPS
            and bbox[2] <= region[2] + EPS and bbox[3] <= region[3] + EPS)


def _union(boxes: list[tuple[float, ...]]) -> list[float]:
    return [round(v, 4) for v in (min(b[0] for b in boxes), min(b[1] for b in boxes),
                                  max(b[2] for b in boxes), max(b[3] for b in boxes))]


def remap_regions(regions: list[dict], old: dict[str, tuple[int, list[float]]],
                  new: dict[str, tuple[int, tuple[float, ...]]]) -> list[dict]:
    """原区域是若干连续单元的外接框：找出落在其中的单元，按新位置重新合并（每页一个框）。"""
    out: list[dict] = []
    for r in regions:
        ids = [bid for bid, (page, bbox) in old.items() if page == r["page"] and _inside(bbox, r["bbox"])]
        by_page: dict[int, list[tuple[float, ...]]] = {}
        for bid in ids:
            page, bbox = new[bid]
            by_page.setdefault(page, []).append(bbox)
        out += [{"page": p, "bbox": _union(bs)} for p, bs in sorted(by_page.items())]
    return out or regions


def candidates(job_ids: list[str] | None, force: bool) -> list[str]:
    store = get_store()
    with SessionLocal() as s:
        stmt = select(ParseJob.id).where(ParseJob.file_type == "docx", ParseJob.parser == "docx",
                                         ParseJob.status == "done").order_by(ParseJob.created_at)
        if job_ids:
            stmt = stmt.where(ParseJob.id.in_(job_ids))
        ids = list(s.scalars(stmt))
    return [j for j in ids if force or not store.exists(f"jobs/{j}/pages/1.png")]


def backfill_job(job_id: str) -> str:
    store = get_store()
    settings = get_settings()
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        if job is None:
            return "任务不存在"
        key = job.file_keys[0]["key"]
        rows = list(s.scalars(select(ParseBlock).where(ParseBlock.job_id == job_id).order_by(ParseBlock.seq)))
        blocks = {r.id: Block(r.seq, r.page, tuple(r.bbox), r.type, r.content) for r in rows}  # type: ignore[arg-type]
        old = {r.id: (r.page, list(r.bbox)) for r in rows}
    if not store.exists(key):
        return "原始文件不存在"
    pdf = docx_to_pdf(store.get(key))
    if pdf is None:
        return "转换失败"
    located = locate_blocks(pdf, list(blocks.values()))
    pages = pdf_page_count(pdf)
    # 渲染下一批的同时并发上传上一批（对象存储逐个上传较慢）
    with ThreadPoolExecutor(PUT_CONCURRENCY) as pool:
        uploads = [pool.submit(store.put, f"jobs/{job_id}/pages/{i}.png", png)
                   for start in range(0, pages, PAGE_BATCH)
                   for i, png in enumerate(render_pages(pdf, settings.page_dpi, start, start + PAGE_BATCH), start + 1)]
        for u in uploads:
            u.result()  # 任一页上传失败则本份失败，不更新数据库，可重跑
    new = {bid: (b.page, b.bbox) for bid, b in blocks.items()}
    with SessionLocal() as s:
        for r in s.scalars(select(ParseBlock).where(ParseBlock.job_id == job_id)):
            if r.id in new:
                r.page, r.bbox = new[r.id][0], [round(v, 4) for v in new[r.id][1]]
        moved: dict[str, int] = {}
        for q in s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id).with_for_update()):
            q.regions = remap_regions(list(q.regions), old, new)
            if q.regions and q.regions[0]["page"] != q.page:
                q.page = moved[q.id] = q.regions[0]["page"]
        if moved:
            for b in s.scalars(select(BankQuestion).where(BankQuestion.source_draft_id.in_(list(moved)))):
                b.source_page = moved[b.source_draft_id]
        job = s.get(ParseJob, job_id)
        if job is not None:
            job.page_count = pages
            if NO_PDF in job.warnings:
                job.warnings = [w for w in job.warnings if w != NO_PDF]
        s.commit()
    return f"{pages} 页，定位 {located}/{len(blocks)} 个单元"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", action="append", help="只处理指定任务，可重复")
    parser.add_argument("--limit", type=int, help="最多处理的份数")
    parser.add_argument("--force", action="store_true", help="已有页面图也重新生成")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("qcloud_cos").setLevel(logging.WARNING)  # 不逐个记录对象存储请求
    ids = candidates(args.job, args.force)[: args.limit]
    log.info("待处理 %d 份", len(ids))
    ok = 0
    for n, job_id in enumerate(ids, 1):
        t = time.monotonic()
        try:
            note = backfill_job(job_id)
            ok += "页" in note
        except Exception as e:  # 单份失败不影响其余
            log.exception("任务 %s 失败", job_id)
            note = f"失败：{e}"
        log.info("[%d/%d] %s %s（%.1f 秒）", n, len(ids), job_id, note, time.monotonic() - t)
    log.info("完成 %d/%d 份", ok, len(ids))


if __name__ == "__main__":
    main()
