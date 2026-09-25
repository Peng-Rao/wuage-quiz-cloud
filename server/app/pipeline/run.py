"""解析流水线：归一化 → 文档解析（ocr）→ 试卷分类 → 拆题 → 知识点 → 难度。"""

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import delete

from ..config import get_settings
from ..db import DraftQuestion, ParseBlock, ParseJob, SessionLocal
from ..storage import get_store
from ..usage import current_job
from . import difficulty
from .classify import classify
from .files import normalize, pdf_page_count, render_pages
from .ir import Block
from .parsers.router import AllParsersFailed, parse_with_fallback
from .segment import segment, units_from_blocks

log = logging.getLogger(__name__)

# 各阶段在总进度中的区间
SPAN = {"ocr": (0, 50), "classify": (50, 58), "segment": (58, 88), "knowledge": (88, 90), "difficulty": (90, 100)}


class UserFacingError(Exception):
    """可直接展示给用户的失败原因。"""


class JobContext:
    def __init__(self, job_id: str):
        self.job_id = job_id

    def update(self, **fields: Any) -> None:
        with SessionLocal() as s:
            job = s.get(ParseJob, self.job_id)
            if job is None:
                raise UserFacingError("任务已被删除")
            for k, v in fields.items():
                setattr(job, k, v)
            s.commit()

    def stage(self, stage: str, status: str, note: str | None = None) -> None:
        with SessionLocal() as s:
            job = s.get(ParseJob, self.job_id)
            assert job is not None
            stages = [dict(x) for x in job.stages]
            for x in stages:
                if x["stage"] == stage:
                    x["status"] = status
                    x["note"] = note
            job.stages = stages
            lo, hi = SPAN[stage]
            job.progress = max(job.progress, hi if status in ("done", "skipped") else lo)
            s.commit()

    def progress(self, stage: str, fraction: float) -> None:
        lo, hi = SPAN[stage]
        pct = int(lo + (hi - lo) * max(0.0, min(1.0, fraction)))
        with SessionLocal() as s:
            job = s.get(ParseJob, self.job_id)
            if job and pct > job.progress:
                job.progress = pct
                s.commit()


def _union(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _regions(block_ids: list[str], blocks: dict[str, Block]) -> list[dict]:
    by_page: dict[int, list[list[float]]] = {}
    for bid in block_ids:
        b = blocks[bid]
        by_page.setdefault(b.page, []).append(list(b.bbox))
    return [{"page": p, "bbox": [round(v, 4) for v in _union(bs)]} for p, bs in sorted(by_page.items())]


def _meta_note(meta: dict) -> str:
    parts = [meta.get("stage"), meta.get("subject"), (meta.get("paper_type") or "").replace("考试", "")]
    return " · ".join(p for p in parts if p) or "未识别"


async def run_job(job_id: str) -> None:
    settings = get_settings()
    store = get_store()
    ctx = JobContext(job_id)
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        if job is None:
            return
        files_info = list(job.file_keys)
        options = dict(job.options)
        file_type = job.file_type
    ctx.update(status="running", error=None, progress=0)
    warnings: list[str] = []

    # ---- ocr：文档解析 ----
    ctx.stage("ocr", "running")
    files = [(f["name"], store.get(f["key"])) for f in files_info]
    src = await asyncio.to_thread(normalize, files)

    async def on_progress(frac: float) -> None:
        ctx.progress("ocr", frac * 0.9)

    try:
        doc, parser, w = await parse_with_fallback(
            src, ocr=options.get("ocr", True) or file_type == "image", settings=settings, on_progress=on_progress,
        )
    except AllParsersFailed as e:
        raise UserFacingError(str(e)) from e
    warnings += w
    page_count = doc.page_count or (pdf_page_count(doc.pdf) if doc.pdf else 0)

    block_ids = [f"b{job_id}_{b.seq}" for b in doc.blocks]
    blocks_by_id = dict(zip(block_ids, doc.blocks))
    with SessionLocal() as s:
        s.execute(delete(DraftQuestion).where(DraftQuestion.job_id == job_id))
        s.execute(delete(ParseBlock).where(ParseBlock.job_id == job_id))
        for bid, b in blocks_by_id.items():
            image_key = None
            if b.image:
                image_key = f"jobs/{job_id}/blocks/{bid}.{b.image_ext}"
                store.put(image_key, b.image)
            s.add(ParseBlock(id=bid, job_id=job_id, seq=b.seq, page=b.page, bbox=list(b.bbox), type=b.type,
                             content=b.content, image_key=image_key, score=b.score))
        s.commit()
    if doc.pdf:
        pages = await asyncio.to_thread(render_pages, doc.pdf, settings.page_dpi)
        for i, png in enumerate(pages, 1):
            store.put(f"jobs/{job_id}/pages/{i}.png", png)
    else:
        warnings.append("解析结果不含原始 PDF，无法查看原图")
    ctx.update(parser=parser, page_count=page_count)
    ctx.stage("ocr", "done", f"识别 {page_count} 页")

    # ---- classify：试卷分类 ----
    ctx.stage("classify", "running")
    head = "\n".join(b.text for b in doc.blocks if b.page == 1 and b.type in ("text", "title"))[:3000]
    meta, w = await classify(head, settings)
    warnings += w
    meta_dict = meta.model_dump()
    ctx.update(meta=meta_dict)
    ctx.stage("classify", "done", _meta_note(meta_dict))

    # ---- segment：拆题 ----
    ctx.stage("segment", "running")
    units = units_from_blocks(doc.blocks, block_ids)
    seg = await segment(units, settings, with_answer=options.get("answer", True))
    warnings += seg.warnings
    if not seg.questions:
        raise UserFacingError("未识别到题目，请确认上传的是试卷，或检查题号格式")
    coefs = difficulty.estimate(seg.questions)
    with SessionLocal() as s:
        for i, (q, coef) in enumerate(zip(seg.questions, coefs), 1):
            regions = _regions(q.unit_ids, blocks_by_id)
            images = [f"jobs/{job_id}/blocks/{bid}.{blocks_by_id[bid].image_ext}"
                      for bid in q.unit_ids if blocks_by_id[bid].image]
            s.add(DraftQuestion(
                id="q" + uuid.uuid4().hex[:20], job_id=job_id, no=i, type=q.type, score=q.score,
                page=regions[0]["page"] if regions else 1, stem=q.stem, options=q.options, answer=q.answer,
                answer_source="paper" if q.answer else None,
                analysis=q.analysis, knowledge_points=[], coef=coef, confidence=q.confidence,
                block_ids=q.unit_ids, regions=regions, images=images, duplicate_of=None, status="draft",
            ))
        s.commit()
    ctx.stage("segment", "done", f"{len(seg.questions)} 道题" + ("" if seg.used_llm else "（规则）"))

    # ---- knowledge：知识点标注（P2） ----
    ctx.stage("knowledge", "skipped", "即将开放" if options.get("knowledge", True) else None)

    # ---- difficulty：难度评估（P1 为基线估计） ----
    ctx.stage("difficulty", "done", "基线估计")
    if options.get("dedupe"):
        warnings.append("题库查重将在后续版本提供")
    ctx.update(status="done", progress=100, warnings=warnings)


async def run_job_safely(job_id: str) -> None:
    ctx = JobContext(job_id)
    # 本任务内的大模型 / MinerU 调用都记到该任务名下
    current_job.set(job_id)
    try:
        await run_job(job_id)
    except UserFacingError as e:
        _fail(ctx, str(e))
    except ValueError as e:  # 文件格式校验等
        _fail(ctx, str(e))
    except Exception:
        log.exception("解析任务 %s 异常", job_id)
        _fail(ctx, "解析服务内部错误，请稍后重试")


def _fail(ctx: JobContext, message: str) -> None:
    with SessionLocal() as s:
        job = s.get(ParseJob, ctx.job_id)
        if job is None:
            return
        job.status = "failed"
        job.error = message
        job.stages = [dict(x, status="failed") if x["status"] == "running" else dict(x) for x in job.stages]
        s.commit()
