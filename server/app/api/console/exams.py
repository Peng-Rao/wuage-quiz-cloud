"""管理后台：查看与管理全部上传的试卷（解析任务），不受学科范围限制。"""

from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ...bank import remove_paper, sync_meta
from ...config import get_settings
from ...console import audit, require
from ...db import AiUsage, BankQuestion, ConsoleAccount, DraftQuestion, EvalSample, ParseJob, SessionLocal, User
from ...schemas import PARSE_STAGES, Model, PaperMeta, StageState, UsageSummary, UtcDatetime
from ...services import current_school
from ...storage import LocalStore, get_store
from ...usage import summarize
from ...worker import worker

router = APIRouter(prefix="/api/admin/exams")
can_manage = require("exams")


class Owner(Model):
    id: str
    username: str
    display_name: str


class ExamItem(Model):
    id: str
    file_name: str
    file_type: str
    file_count: int
    file_size: int
    title: str
    subject: str | None
    status: str
    progress: int
    page_count: int | None
    parser: str | None
    owner: Owner | None
    question_count: int
    saved_count: int
    bank_count: int
    calls: int
    cost: float | None
    currency: str
    error: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ExamPage(Model):
    items: list[ExamItem]
    total: int
    by_status: dict[str, int]


class ExamQuestion(Model):
    id: str
    no: int
    type: str
    score: float
    page: int
    stem: str
    status: str
    confidence: float
    has_answer: bool
    knowledge: list[str]


class ExamFile(Model):
    index: int
    name: str
    key: str


class ExamDetail(ExamItem):
    meta: PaperMeta | None
    options: dict[str, Any]
    stages: list[StageState]
    warnings: list[Any]
    files: list[ExamFile]
    usage: UsageSummary | None
    questions: list[ExamQuestion]


def _subject():
    return func.coalesce(func.nullif(ParseJob.meta["subject"].as_string(), ""), ParseJob.subject_scope)


def _get(s: Session, job_id: str) -> ParseJob:
    job = s.get(ParseJob, job_id)
    if job is None or job.school_id != current_school():
        raise HTTPException(404, "试卷不存在")
    return job


def _items(s: Session, jobs: list[ParseJob]) -> list[ExamItem]:
    ids = [j.id for j in jobs]
    if not ids:
        return []
    drafts = dict(s.execute(select(DraftQuestion.job_id, func.count()).where(DraftQuestion.job_id.in_(ids))
                            .group_by(DraftQuestion.job_id)).all())
    saved = dict(s.execute(select(DraftQuestion.job_id, func.count()).where(
        DraftQuestion.job_id.in_(ids), DraftQuestion.status == "saved").group_by(DraftQuestion.job_id)).all())
    bank = dict(s.execute(select(BankQuestion.source_job_id, func.count()).where(BankQuestion.source_job_id.in_(ids))
                          .group_by(BankQuestion.source_job_id)).all())
    owners = {u.id: u for u in s.scalars(select(User).where(User.id.in_({j.owner_id for j in jobs if j.owner_id})))}
    usage: dict[str, list[AiUsage]] = {}
    for u in s.scalars(select(AiUsage).where(AiUsage.job_id.in_(ids))):
        usage.setdefault(u.job_id, []).append(u)
    out = []
    for j in jobs:
        summary = summarize(usage.get(j.id, []))
        owner = owners.get(j.owner_id) if j.owner_id else None
        meta = j.meta or {}
        out.append(ExamItem(
            id=j.id, file_name=j.file_name, file_type=j.file_type, file_count=j.file_count, file_size=j.file_size,
            title=meta.get("title") or "", subject=meta.get("subject") or j.subject_scope, status=j.status,
            progress=j.progress, page_count=j.page_count, parser=j.parser,
            owner=Owner(id=owner.id, username=owner.username, display_name=owner.display_name) if owner else None,
            question_count=drafts.get(j.id, 0), saved_count=saved.get(j.id, 0), bank_count=bank.get(j.id, 0),
            calls=summary.calls, cost=summary.cost, currency=summary.currency, error=j.error,
            created_at=j.created_at, updated_at=j.updated_at))
    return out


@router.get("", response_model=ExamPage)
def list_exams(
    q: str | None = Query(None, max_length=100),
    status: str | None = Query(None, description="逗号分隔"),
    subject: str | None = Query(None, max_length=16),
    owner: str | None = Query(None, max_length=32),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: ConsoleAccount = Depends(can_manage),
) -> ExamPage:
    with SessionLocal() as s:
        base = select(ParseJob).where(ParseJob.school_id == current_school(), ParseJob.kind.is_(None))
        if q and q.strip():
            like = f"%{q.strip()}%"
            base = base.where(or_(ParseJob.file_name.ilike(like), ParseJob.meta["title"].as_string().ilike(like),
                                  ParseJob.id == q.strip()))
        if subject:
            base = base.where(_subject() == subject)
        if owner:
            base = base.where(ParseJob.owner_id == owner)
        # 各状态的数量（不受状态筛选影响），用于筛选标签
        sub = base.subquery()
        counts = dict(s.execute(select(sub.c.status, func.count()).group_by(sub.c.status)).all())
        if status:
            base = base.where(ParseJob.status.in_([x.strip() for x in status.split(",") if x.strip()]))
        total = s.scalar(select(func.count()).select_from(base.subquery())) or 0
        jobs = list(s.scalars(base.order_by(ParseJob.created_at.desc()).limit(limit).offset(offset)))
        return ExamPage(items=_items(s, jobs), total=total, by_status=counts)


@router.get("/{job_id}", response_model=ExamDetail)
def read_exam(job_id: str, _: ConsoleAccount = Depends(can_manage)) -> ExamDetail:
    with SessionLocal() as s:
        job = _get(s, job_id)
        item = _items(s, [job])[0]
        rows = list(s.scalars(select(AiUsage).where(AiUsage.job_id == job.id)))
        qs = s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job.id).order_by(DraftQuestion.no))
        return ExamDetail(
            **item.model_dump(), meta=job.meta, options=job.options or {}, stages=job.stages, warnings=job.warnings or [],
            files=[ExamFile(index=i, name=f.get("name") or PurePosixPath(f["key"]).name, key=f["key"])
                   for i, f in enumerate(job.file_keys or [])],
            usage=summarize(rows) if rows else None,
            questions=[ExamQuestion(id=d.id, no=d.no, type=d.type, score=d.score, page=d.page, stem=d.stem,
                                    status=d.status, confidence=d.confidence, has_answer=bool(d.answer),
                                    knowledge=[k.get("name", "") for k in (d.knowledge_points or [])])
                       for d in qs])


@router.get("/{job_id}/files/{index}")
def download_file(job_id: str, index: int, _: ConsoleAccount = Depends(can_manage)) -> Response:
    """下载上传的原始文件。"""
    with SessionLocal() as s:
        job = _get(s, job_id)
        files = job.file_keys or []
        if not 0 <= index < len(files):
            raise HTTPException(404, "文件不存在")
        key, name = files[index]["key"], files[index].get("name") or "file"
    store = get_store()
    if not key.startswith("uploads/") or not store.exists(key):
        raise HTTPException(404, "文件不存在或已被清理")
    disposition = f"attachment; filename*=UTF-8''{quote(name)}"
    if isinstance(store, LocalStore):
        return FileResponse(store.path(key), headers={"Content-Disposition": disposition,
                                                      "Cache-Control": "private, no-store"})
    return RedirectResponse(store.signed_url(key, get_settings().storage_url_expires), status_code=302)


@router.put("/{job_id}/meta", response_model=PaperMeta)
def update_meta(job_id: str, meta: PaperMeta, actor: ConsoleAccount = Depends(can_manage)) -> PaperMeta:
    """修改试卷属性，同步到已入库题目的快照（改了学科的题撤销审核）。"""
    meta = meta.model_copy(update={k: v.strip() for k, v in meta.model_dump().items()})
    with SessionLocal() as s:
        job = _get(s, job_id)
        job.meta = meta.model_dump()
        sync_meta(s, job)
        audit(s, actor, "exam.meta", f"{job.id} {job.file_name}", {"title": meta.title, "subject": meta.subject})
        s.commit()
    return meta


@router.post("/{job_id}/retry", response_model=ExamItem)
def retry(job_id: str, actor: ConsoleAccount = Depends(can_manage)) -> ExamItem:
    with SessionLocal() as s:
        job = _get(s, job_id)
        if job.status not in ("failed", "cancelled"):
            raise HTTPException(400, "只能重试失败或已取消的试卷")
        job.status, job.error, job.progress, job.parser = "queued", None, 0, None
        job.stages = [{"stage": st, "status": "pending", "note": None} for st in PARSE_STAGES]
        audit(s, actor, "exam.retry", f"{job.id} {job.file_name}")
        s.commit()
        worker.enqueue(job.id)
        return _items(s, [job])[0]


@router.post("/{job_id}/cancel", response_model=ExamItem)
def cancel(job_id: str, actor: ConsoleAccount = Depends(can_manage)) -> ExamItem:
    with SessionLocal() as s:
        job = _get(s, job_id)
        if job.status != "queued":
            raise HTTPException(400, "只能取消排队中的试卷")
        job.status, job.error = "cancelled", "已取消"
        audit(s, actor, "exam.cancel", f"{job.id} {job.file_name}")
        s.commit()
        return _items(s, [job])[0]


@router.post("/{job_id}/unpublish")
def unpublish(job_id: str, actor: ConsoleAccount = Depends(can_manage)) -> dict[str, int]:
    """移出试卷库：删除已入库的题，草稿保留，可在核对页重新入库。"""
    with SessionLocal() as s:
        job = _get(s, job_id)
        removed = remove_paper(s, current_school(), job.id)
        audit(s, actor, "exam.unpublish", f"{job.id} {job.file_name}", {"removed": removed})
        s.commit()
    return {"removed": removed}


@router.delete("/{job_id}", status_code=204)
def delete_exam(job_id: str, actor: ConsoleAccount = Depends(can_manage)) -> None:
    """彻底删除试卷：解析结果、草稿与已入库的题一并删除（AI 用量记录保留，成本统计不变）。"""
    with SessionLocal() as s:
        job = _get(s, job_id)
        if job.status in ("queued", "running") or (job.answer_task or {}).get("status") in ("queued", "running"):
            raise HTTPException(409, "试卷正在处理中，请先取消或等待完成")
        if s.scalar(select(EvalSample.id).where(EvalSample.job_id == job.id)):
            raise HTTPException(409, "该试卷是解析评测样本，请先在「解析评测」中移除")
        removed = s.execute(delete(BankQuestion).where(BankQuestion.source_job_id == job.id)).rowcount or 0
        audit(s, actor, "exam.delete", f"{job.id} {job.file_name}", {"bankQuestions": removed})
        # parse_block、draft_question 随任务级联删除
        s.execute(delete(ParseJob).where(ParseJob.id == job.id))
        s.commit()
