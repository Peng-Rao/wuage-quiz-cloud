import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import BankQuestion, ParseJob, SessionLocal, get_session
from ..pipeline.files import validate_kinds
from ..schemas import (
    PARSE_STAGES, AnswerTask, CommitRequest, CommitResult, CreateJobRequest, DraftQuestionOut, GenerateAnswersRequest,
    JobUsage, PaperMeta, ParseJobOut, RecentUpload,
)
from ..config import get_settings
from ..pipeline.answer import pick_questions
from ..usage import calls_out, job_rows, summarize
from ..services import current_school, get_job, job_out, list_questions, question_out, recent_out
from ..storage import get_store
from ..worker import worker

router = APIRouter(prefix="/api/parse-jobs")


@router.post("", response_model=ParseJobOut)
def create_job(req: CreateJobRequest, s: Session = Depends(get_session)) -> ParseJobOut:
    if len(req.file_keys) != len(req.file_names):
        raise HTTPException(400, "fileKeys 与 fileNames 数量不一致")
    try:
        file_type = validate_kinds(req.file_names)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    store = get_store()
    sizes = []
    for key in req.file_keys:
        if not key.startswith("uploads/") or not store.exists(key):
            raise HTTPException(400, "文件尚未上传完成")
        sizes.append(store.size(key))
    names = req.file_names
    job = ParseJob(
        id=uuid.uuid4().hex[:16],
        school_id=current_school(),
        file_name=f"{names[0]} 等 {len(names)} 个文件" if len(names) > 1 else names[0],
        file_count=len(names),
        file_size=sum(sizes),
        file_type=file_type,
        file_keys=[{"key": k, "name": n} for k, n in zip(req.file_keys, names)],
        options=req.options.model_dump(),
        status="queued",
        progress=0,
        stages=[{"stage": st, "status": "pending", "note": None} for st in PARSE_STAGES],
        warnings=[],
    )
    s.add(job)
    s.commit()
    worker.enqueue(job.id)
    return job_out(s, job)


@router.get("", response_model=list[RecentUpload])
def list_recent(s: Session = Depends(get_session)) -> list[RecentUpload]:
    jobs = s.scalars(
        select(ParseJob).where(ParseJob.school_id == current_school(), ParseJob.status == "done")
        .order_by(ParseJob.created_at.desc()).limit(5)
    )
    return [recent_out(s, j) for j in jobs]


@router.get("/{job_id}", response_model=ParseJobOut)
def read_job(job_id: str, s: Session = Depends(get_session)) -> ParseJobOut:
    return job_out(s, get_job(s, job_id))


@router.post("/{job_id}/generate-answers", response_model=AnswerTask, status_code=202)
def generate_answers(job_id: str, req: GenerateAnswersRequest, s: Session = Depends(get_session)) -> AnswerTask:
    """为缺少答案的题（或指定的题）排队生成 AI 答案，进度见 ParseJob.answerTask。"""
    job = get_job(s, job_id)
    if job.status != "done":
        raise HTTPException(400, "解析尚未完成")
    if not get_settings().llm_enabled:
        raise HTTPException(400, "未配置大模型，无法生成答案")
    if (job.answer_task or {}).get("status") in ("queued", "running"):
        raise HTTPException(409, "正在生成答案，请等待当前任务完成")
    qids = pick_questions(job_id, req.question_ids, req.overwrite)
    if not qids:
        raise HTTPException(400, "没有需要生成答案的题目")
    job.answer_task = {"status": "queued", "total": len(qids), "done": 0, "failed": 0, "error": None,
                       "questionIds": qids, "overwrite": req.overwrite}
    s.commit()
    worker.enqueue_answers(job_id)
    return AnswerTask.model_validate(job.answer_task)


@router.get("/{job_id}/usage", response_model=JobUsage)
def read_usage(job_id: str, s: Session = Depends(get_session)) -> JobUsage:
    get_job(s, job_id)
    rows = job_rows(s, job_id)
    return JobUsage(summary=summarize(rows), calls=calls_out(rows))


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    with SessionLocal() as s:
        get_job(s, job_id)

    async def stream():
        last = None
        idle = 0.0
        while not await request.is_disconnected():
            with SessionLocal() as s:
                job = s.get(ParseJob, job_id)
                if job is None:
                    return
                payload = job_out(s, job).model_dump_json(by_alias=True)
                status = job.status
            if payload != last:
                last, idle = payload, 0.0
                yield f"data: {payload}\n\n"
            elif idle >= 15:
                idle = 0.0
                yield ": keep-alive\n\n"
            if status in ("done", "failed"):
                return
            await asyncio.sleep(0.5)
            idle += 0.5

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.put("/{job_id}/meta", response_model=PaperMeta)
def update_meta(job_id: str, meta: PaperMeta, s: Session = Depends(get_session)) -> PaperMeta:
    job = get_job(s, job_id)
    job.meta = meta.model_dump()
    s.commit()
    return meta


@router.get("/{job_id}/questions", response_model=list[DraftQuestionOut])
def read_questions(job_id: str, s: Session = Depends(get_session)) -> list[DraftQuestionOut]:
    get_job(s, job_id)
    return [question_out(q) for q in list_questions(s, job_id)]


@router.post("/{job_id}/commit", response_model=CommitResult)
def commit(job_id: str, req: CommitRequest, s: Session = Depends(get_session)) -> CommitResult:
    job = get_job(s, job_id)
    if job.status != "done":
        raise HTTPException(400, "解析尚未完成")
    wanted = set(req.question_ids)
    qs = [q for q in list_questions(s, job_id) if q.id in wanted]
    if len(qs) != len(wanted):
        raise HTTPException(400, "部分题目不存在，请刷新后重试")
    existing = {b.source_draft_id: b for b in s.scalars(
        select(BankQuestion).where(BankQuestion.source_draft_id.in_(wanted)))}
    for q in qs:
        b = existing.get(q.id) or BankQuestion(id="k" + uuid.uuid4().hex[:20], school_id=job.school_id,
                                               source_job_id=job_id, source_draft_id=q.id)
        b.type, b.score, b.stem, b.options = q.type, q.score, q.stem, q.options
        b.answer, b.analysis, b.knowledge_points, b.coef = q.answer, q.analysis, q.knowledge_points, q.coef
        b.answer_source = q.answer_source
        b.images, b.meta = q.images, job.meta
        s.add(b)
        q.status = "saved"
    s.commit()
    return CommitResult(saved_count=len(qs))
