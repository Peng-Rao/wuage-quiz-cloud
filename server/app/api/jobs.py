import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..bank import commit_questions, sync_meta
from ..auth import current_user, require_subject, scope_session
from ..db import ParseJob, SessionLocal, get_session
from ..schemas import (
    PARSE_STAGES, AnswerTask, CommitRequest, CommitResult, CreateJobRequest, DraftQuestionOut, GenerateAnswersRequest,
    JobListPage, JobUsage, PaperMeta, ParseJobOut, RecentUpload,
)
from ..config import get_settings
from ..pipeline.answer import pick_questions
from ..usage import calls_out, job_rows, summarize
from ..services import current_school, get_job, job_out, list_item, list_questions, new_job, question_out, recent_out
from ..worker import worker

router = APIRouter(prefix="/api/parse-jobs")


@router.post("", response_model=ParseJobOut)
def create_job(req: CreateJobRequest, s: Session = Depends(get_session)) -> ParseJobOut:
    job = new_job(s, req.file_keys, req.file_names, req.options)
    s.commit()
    worker.enqueue(job.id)
    return job_out(s, job)


@router.get("", response_model=list[RecentUpload] | JobListPage)
def list_jobs(
    recent: bool = False,
    status: str | None = Query(None, description="逗号分隔，如 queued,running"),
    batch_id: str | None = Query(None, alias="batchId"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
) -> list[RecentUpload] | JobListPage:
    """recent=1：最近完成的 5 份（兼容旧接口）；否则返回分页的任务列表，最新的在前。"""
    school = current_school()
    if recent:
        jobs = s.scalars(select(ParseJob).where(ParseJob.school_id == school, ParseJob.status == "done",
                                                ParseJob.kind.is_(None)).order_by(ParseJob.created_at.desc()).limit(5))
        return [recent_out(s, j) for j in jobs]
    # 评测任务不出现在任务列表中
    q = select(ParseJob).where(ParseJob.school_id == school, ParseJob.kind.is_(None))
    if status:
        q = q.where(ParseJob.status.in_([x.strip() for x in status.split(",") if x.strip()]))
    if batch_id:
        q = q.where(ParseJob.batch_id == batch_id)
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    active = s.scalar(select(func.count()).where(ParseJob.school_id == school, ParseJob.kind.is_(None),
                                                 ParseJob.status.in_(["queued", "running"]))) or 0
    jobs = s.scalars(q.order_by(ParseJob.created_at.desc()).limit(limit).offset(offset))
    return JobListPage(items=[list_item(s, j) for j in jobs], total=total, active=active)


@router.post("/{job_id}/cancel", response_model=ParseJobOut)
def cancel_job(job_id: str, s: Session = Depends(get_session)) -> ParseJobOut:
    """取消排队中的任务；已开始解析的任务不可取消。"""
    job = get_job(s, job_id)
    if job.status != "queued":
        raise HTTPException(400, "只能取消排队中的任务")
    job.status = "cancelled"
    job.error = "已取消"
    s.commit()
    return job_out(s, job)


@router.post("/{job_id}/retry", response_model=ParseJobOut)
def retry_job(job_id: str, s: Session = Depends(get_session)) -> ParseJobOut:
    """重新解析失败或已取消的任务（草稿题会被清空重建）。"""
    job = get_job(s, job_id)
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(400, "只能重试失败或已取消的任务")
    job.status, job.error, job.progress, job.parser = "queued", None, 0, None
    job.stages = [{"stage": st, "status": "pending", "note": None} for st in PARSE_STAGES]
    s.commit()
    worker.enqueue(job.id)
    return job_out(s, job)


@router.get("/{job_id}", response_model=ParseJobOut)
def read_job(job_id: str, s: Session = Depends(get_session)) -> ParseJobOut:
    return job_out(s, get_job(s, job_id))


def check_answer_task(job: ParseJob) -> None:
    """能否发起 AI 生成答案：本卷同一时间只有一个生成答案任务（草稿题与已入库的题共用）。"""
    if job.status != "done":
        raise HTTPException(400, "解析尚未完成")
    if not get_settings().llm_enabled:
        raise HTTPException(400, "未配置大模型，无法生成答案")
    if (job.answer_task or {}).get("status") in ("queued", "running"):
        raise HTTPException(409, "正在生成答案，请等待当前任务完成")


def queue_answer_task(s: Session, job: ParseJob, qids: list[str], overwrite: bool = False,
                      scope: str = "draft") -> AnswerTask:
    if not qids:
        raise HTTPException(400, "没有需要生成答案的题目")
    job.answer_task = {"status": "queued", "total": len(qids), "done": 0, "failed": 0, "error": None,
                       "questionIds": qids, "overwrite": overwrite, "scope": scope}
    s.commit()
    worker.enqueue_answers(job.id)
    return AnswerTask.model_validate(job.answer_task)


@router.post("/{job_id}/generate-answers", response_model=AnswerTask, status_code=202)
def generate_answers(job_id: str, req: GenerateAnswersRequest, s: Session = Depends(get_session)) -> AnswerTask:
    """为缺少答案的题（或指定的题）排队生成 AI 答案，进度见 ParseJob.answerTask。"""
    job = get_job(s, job_id)
    check_answer_task(job)
    return queue_answer_task(s, job, pick_questions(job_id, req.question_ids, req.overwrite), req.overwrite)


@router.post("/{job_id}/tag-knowledge", response_model=ParseJobOut, status_code=202)
def tag_knowledge(job_id: str, s: Session = Depends(get_session)) -> ParseJobOut:
    """为尚未标注知识点的题补标（后台执行，进度见 stages 中 knowledge 阶段的状态）。"""
    job = get_job(s, job_id)
    if job.status != "done":
        raise HTTPException(400, "解析尚未完成")
    if not get_settings().llm_enabled:
        raise HTTPException(400, "未配置大模型，无法标注知识点")
    stage = next((x for x in job.stages if x["stage"] == "knowledge"), None)
    if stage and stage["status"] == "running":
        raise HTTPException(409, "正在标注知识点")
    if all(q.knowledge_points for q in list_questions(s, job_id)):
        raise HTTPException(400, "所有题目都已标注知识点")
    job.stages = [dict(x, status="running", note=None) if x["stage"] == "knowledge" else dict(x) for x in job.stages]
    s.commit()
    worker.enqueue_knowledge(job_id)
    return job_out(s, job)


@router.get("/{job_id}/usage", response_model=JobUsage)
def read_usage(job_id: str, s: Session = Depends(get_session)) -> JobUsage:
    get_job(s, job_id)
    rows = job_rows(s, job_id)
    return JobUsage(summary=summarize(rows), calls=calls_out(rows))


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    def snapshot() -> tuple[str, str]:
        with SessionLocal() as s:
            scope_session(s, current_user(request))
            job = get_job(s, job_id)
            return job_out(s, job).model_dump_json(by_alias=True), job.status

    initial = await asyncio.to_thread(snapshot)

    async def stream():
        last = None
        idle = 0.0
        current = initial
        while not await request.is_disconnected():
            payload, status = current
            if payload != last:
                last, idle = payload, 0.0
                yield f"data: {payload}\n\n"
            elif idle >= 15:
                idle = 0.0
                yield ": keep-alive\n\n"
            if status in ("done", "failed", "cancelled"):
                return
            interval = 5.0 if status == "queued" else 2.0
            await asyncio.sleep(interval)
            idle += interval
            if await request.is_disconnected():
                return
            try:
                current = await asyncio.to_thread(snapshot)
            except HTTPException:  # 会话失效、权限撤回或任务被删除
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.put("/{job_id}/meta", response_model=PaperMeta)
def update_meta(job_id: str, meta: PaperMeta, s: Session = Depends(get_session)) -> PaperMeta:
    job = get_job(s, job_id)
    require_subject(s.info["user"], meta.subject)
    job.meta = meta.model_dump()
    sync_meta(s, job)
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
    result = commit_questions(s, job, qs, s.info["user"].id, force=req.force)
    s.commit()  # 整份试卷重复时也保存补算的文件指纹
    return result
