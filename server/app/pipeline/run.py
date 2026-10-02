"""解析流水线：归一化 → 文档解析（ocr）→ 试卷分类 → 拆题 → 知识点 → 难度。"""

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import delete

from ..config import get_settings
from ..db import DraftQuestion, ParseBlock, ParseJob, SessionLocal
from ..similar import dedupe_job
from ..storage import ObjectStore, get_store
from ..usage import current_job
from . import difficulty
from .classify import classify
from .files import normalize, pdf_page_count, render_pages
from .knowledge import tag_job
from .ir import Block
from .parsers.router import AllParsersFailed, build_parser, parse_with_fallback
from .segment import segment, units_from_blocks

log = logging.getLogger(__name__)

# 各阶段在总进度中的区间
SPAN = {"ocr": (0, 50), "classify": (50, 58), "segment": (58, 86), "knowledge": (86, 88), "difficulty": (88, 90),
        "dedupe": (90, 100)}


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


# 同时上传的文件数（对象存储为网络请求，逐个上传较慢）
PUT_CONCURRENCY = 8


async def put_all(store: ObjectStore, items: list[tuple[str, bytes]]) -> None:
    """在线程中保存文件，不阻塞事件循环（Worker 心跳等在同一事件循环中）。"""
    sem = asyncio.Semaphore(PUT_CONCURRENCY)

    async def one(key: str, data: bytes) -> None:
        async with sem:
            await asyncio.to_thread(store.put, key, data)

    await asyncio.gather(*(one(k, d) for k, d in items))


async def run_job(job_id: str) -> None:
    settings = get_settings()
    store = get_store()
    ctx = JobContext(job_id)
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        if job is None or job.status in ("cancelled", "done"):
            return  # 排队期间被取消，或重启恢复时已完成
        files_info = list(job.file_keys)
        options = dict(job.options)
        kind = job.kind
        file_type = job.file_type
        file_name = job.file_name
    ctx.update(status="running", error=None, progress=0)
    warnings: list[str] = []

    # ---- ocr：文档解析 ----
    ctx.stage("ocr", "running")
    files = [(f["name"], await asyncio.to_thread(store.get, f["key"])) for f in files_info]
    src = await asyncio.to_thread(normalize, files)

    async def on_progress(frac: float) -> None:
        ctx.progress("ocr", frac * 0.9)

    async def ocr(exclude: tuple[str, ...] = ()) -> tuple[Any, list[str], dict[str, Block], str]:
        """解析文档并保存版面单元、配图与页面图。exclude 中的引擎跳过（本地解析无法识别时改用 MinerU）。"""
        names = settings.docx_parser_chain if src.kind == "docx" else settings.parser_chain
        parsers = [build_parser(n, settings) for n in names if n not in exclude] if exclude else None
        if parsers == []:
            raise UserFacingError("没有可用的解析引擎")
        try:
            doc, parser, w = await parse_with_fallback(
                src, ocr=options.get("ocr", True) or file_type == "image", settings=settings,
                on_progress=on_progress, parsers=parsers,
            )
        except AllParsersFailed as e:
            raise UserFacingError(str(e)) from e
        warnings.extend(w)
        page_count = doc.page_count or (pdf_page_count(doc.pdf) if doc.pdf else 0)

        block_ids = [f"b{job_id}_{b.seq}" for b in doc.blocks]
        blocks_by_id = dict(zip(block_ids, doc.blocks))
        image_keys = {bid: f"jobs/{job_id}/blocks/{bid}.{b.image_ext}" for bid, b in blocks_by_id.items() if b.image}
        # 先保存图片再写入版面单元：题目引用的配图一定已存在
        await put_all(store, [(image_keys[bid], blocks_by_id[bid].image) for bid in image_keys])
        with SessionLocal() as s:
            s.execute(delete(DraftQuestion).where(DraftQuestion.job_id == job_id))
            s.execute(delete(ParseBlock).where(ParseBlock.job_id == job_id))
            for bid, b in blocks_by_id.items():
                s.add(ParseBlock(id=bid, job_id=job_id, seq=b.seq, page=b.page, bbox=list(b.bbox), type=b.type,
                                 content=b.content, image_key=image_keys.get(bid), score=b.score))
            s.commit()
        if doc.pdf:
            if "解析结果不含原始 PDF，无法查看原图" in warnings:
                warnings.remove("解析结果不含原始 PDF，无法查看原图")
            pages = await asyncio.to_thread(render_pages, doc.pdf, settings.page_dpi)
            await put_all(store, [(f"jobs/{job_id}/pages/{i}.png", png) for i, png in enumerate(pages, 1)])
        elif "解析结果不含原始 PDF，无法查看原图" not in warnings:
            warnings.append("解析结果不含原始 PDF，无法查看原图")
        ctx.update(parser=parser, page_count=page_count)
        ctx.stage("ocr", "done", f"识别 {page_count} 页")
        return doc, block_ids, blocks_by_id, parser

    doc, block_ids, blocks_by_id, parser = await ocr()

    # ---- classify：试卷分类 ----
    ctx.stage("classify", "running")
    head = "\n".join(b.text for b in doc.blocks if b.page == 1 and b.type in ("text", "title"))[:3000]
    meta, w = await classify(head, settings, file_name=file_name)
    warnings += w
    scope = options.get("subject")
    if scope and meta.subject and meta.subject != scope:
        raise UserFacingError(f"识别学科为{meta.subject}，与上传学科{scope}不一致，请切换学科后上传")
    if scope and not meta.subject:
        meta.subject = scope
    meta_dict = meta.model_dump()
    ctx.update(meta=meta_dict)
    ctx.stage("classify", "done", _meta_note(meta_dict))

    # ---- segment：拆题 ----
    ctx.stage("segment", "running")
    units = units_from_blocks(doc.blocks, block_ids)
    seg = await segment(units, settings, with_answer=options.get("answer", True))
    if not seg.questions and parser == "docx":
        # 本地解析的内容识别不出题目（如排版特殊、题号是图片）：改用解析链中的其他引擎（MinerU）重新识别
        log.info("任务 %s 本地 Word 解析未识别到题目，改用其他解析引擎", job_id)
        warnings.append("本地 Word 解析未识别到题目，已改用 MinerU 识别")
        ctx.stage("ocr", "running")
        doc, block_ids, blocks_by_id, parser = await ocr(exclude=("docx",))
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
                analysis=q.analysis, knowledge_points=[], coef=coef, difficulty_source="baseline", confidence=q.confidence,
                block_ids=q.unit_ids, regions=regions, images=images, duplicate_of=None, status="draft",
            ))
        s.commit()
    ctx.stage("segment", "done", f"{len(seg.questions)} 道题" + ("" if seg.used_llm else "（规则）"))

    # ---- knowledge：知识点标注（同一次调用给出难度评估） ----
    ai_difficulty: dict[str, float] | None = None
    if not options.get("knowledge", True):
        ctx.stage("knowledge", "skipped")
    elif not settings.llm_enabled:
        ctx.stage("knowledge", "skipped", "未配置大模型")
    else:
        ctx.stage("knowledge", "running")
        r = await tag_job(job_id)
        ai_difficulty = r.difficulty
        note = f"{r.kinds} 个知识点" if r.tagged else "未能标注"
        if r.tree_name:
            note += f" · {r.tree_name}" + (f"（{r.unmatched} 个不在知识树中）" if r.unmatched else "")
        ctx.stage("knowledge", "done", note)
        if r.tagged < len(seg.questions):
            warnings.append(f"{len(seg.questions) - r.tagged} 道题未能标注知识点，可在核对页重新标注")

    # ---- difficulty：难度评估（大模型 + 基线，可校准） ----
    ctx.stage("difficulty", "running")
    if ai_difficulty is None and settings.llm_enabled:
        with SessionLocal() as s:
            meta_now = dict(s.get(ParseJob, job_id).meta or {})  # type: ignore[union-attr]
        ai_difficulty = await difficulty.estimate_ai(job_id, meta_now)
    n_ai = difficulty.apply(job_id, ai_difficulty or {})
    ctx.stage("difficulty", "done", f"AI 评估 {n_ai} 题" if n_ai else "基线估计")

    # ---- dedupe：与校本题库查重（评测任务不查重） ----
    if options.get("dedupe", True) and kind != "eval":
        ctx.stage("dedupe", "running")
        with SessionLocal() as s:
            dup = await dedupe_job(s, job_id)
        ctx.stage("dedupe", "done", f"{dup} 道疑似重复" if dup else "未发现重复")
    else:
        ctx.stage("dedupe", "skipped")
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


async def run_knowledge_task(job_id: str) -> None:
    """为已解析的试卷补标知识点（如解析时未启用大模型）。进度体现在 knowledge 阶段的状态上。"""
    current_job.set(job_id)
    ctx = JobContext(job_id)
    try:
        r = await tag_job(job_id, only_missing=True)
        note = f"补标 {r.tagged} 题 · {r.kinds} 个知识点" if r.tagged else "未能标注"
        if r.tree_name:
            note += f" · {r.tree_name}"
        ctx.stage("knowledge", "done", note)
    except Exception:
        log.exception("知识点标注任务 %s 异常", job_id)
        ctx.stage("knowledge", "failed", "标注失败，请重试")
