"""接口层共用的查询、序列化与草稿题编辑逻辑。"""

import re
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from .db import DraftQuestion, EvalSample, ParseJob
from .pipeline.files import validate_kinds
from .schemas import (
    PARSE_STAGES, REVIEW_CONFIDENCE, DraftQuestionOut, JobListItem, ParseJobOut, ParseOptions, QuestionSource,
    RecentUpload,
)
from .storage import get_store

# P1 未接入账号体系，所有数据归属演示学校
DEMO_SCHOOL = "demo"


def current_school() -> str:
    return DEMO_SCHOOL


def not_found(what: str = "资源") -> HTTPException:
    return HTTPException(404, f"{what}不存在")


def get_job(s: Session, job_id: str) -> ParseJob:
    job = s.get(ParseJob, job_id)
    if job is None or job.school_id != current_school():
        raise not_found("解析任务")
    return job


def get_question(s: Session, qid: str) -> DraftQuestion:
    q = s.get(DraftQuestion, qid)
    if q is None:
        raise not_found("题目")
    get_job(s, q.job_id)
    return q


def _counts(s: Session, job_id: str) -> tuple[int, int, int]:
    row = s.execute(
        select(
            func.count(DraftQuestion.id),
            func.count().filter(DraftQuestion.confidence < REVIEW_CONFIDENCE),
            func.count().filter(DraftQuestion.status == "saved"),
        ).where(DraftQuestion.job_id == job_id)
    ).one()
    return int(row[0]), int(row[1]), int(row[2])


def job_out(s: Session, job: ParseJob) -> ParseJobOut:
    from .usage import job_rows, summarize  # 避免循环导入

    total, review, saved = _counts(s, job.id)
    rows = job_rows(s, job.id)
    return ParseJobOut(
        id=job.id, batch_id=job.batch_id, file_name=job.file_name, file_count=job.file_count, file_size=job.file_size,
        file_type=job.file_type, page_count=job.page_count, options=job.options, parser=job.parser,
        status=job.status, progress=job.progress, stages=job.stages, meta=job.meta,
        question_count=total, review_count=review, saved_count=saved, error=job.error,
        usage=summarize(rows) if rows else None, answer_task=job.answer_task,
        eval_sample_id=s.scalar(select(EvalSample.id).where(EvalSample.job_id == job.id)), created_at=job.created_at,
    )


def list_item(s: Session, job: ParseJob) -> JobListItem:
    total, review, saved = _counts(s, job.id)
    current = next((x["stage"] for x in job.stages if x["status"] == "running"), None)
    return JobListItem(
        id=job.id, batch_id=job.batch_id, file_name=job.file_name, file_type=job.file_type, status=job.status,
        progress=job.progress, current_stage=current, question_count=total, review_count=review, saved_count=saved,
        error=job.error, created_at=job.created_at,
    )


def new_job(s: Session, file_keys: list[str], file_names: list[str], options: ParseOptions,
            batch_id: str | None = None) -> ParseJob:
    """校验上传的文件并创建排队中的解析任务（未提交事务，由调用方提交后入队）。"""
    if len(file_keys) != len(file_names):
        raise HTTPException(400, "fileKeys 与 fileNames 数量不一致")
    try:
        file_type = validate_kinds(file_names)
    except ValueError as e:
        raise HTTPException(400, f"{file_names[0]}：{e}" if batch_id else str(e)) from e
    store = get_store()
    sizes = []
    for key in file_keys:
        if not key.startswith("uploads/") or not store.exists(key):
            raise HTTPException(400, "文件尚未上传完成")
        sizes.append(store.size(key))
    job = ParseJob(
        id=uuid.uuid4().hex[:16],
        school_id=current_school(),
        batch_id=batch_id,
        file_name=f"{file_names[0]} 等 {len(file_names)} 个文件" if len(file_names) > 1 else file_names[0],
        file_count=len(file_names),
        file_size=sum(sizes),
        file_type=file_type,
        file_keys=[{"key": k, "name": n} for k, n in zip(file_keys, file_names)],
        options=options.model_dump(),
        status="queued",
        progress=0,
        stages=[{"stage": st, "status": "pending", "note": None} for st in PARSE_STAGES],
        warnings=[],
    )
    s.add(job)
    return job


def recent_out(s: Session, job: ParseJob) -> RecentUpload:
    total, review, saved = _counts(s, job.id)
    return RecentUpload(job_id=job.id, file_name=job.file_name, question_count=total, review_count=review,
                        saved_count=saved, created_at=job.created_at)


def question_source(meta: dict | None, file_name: str | None, no: int | None, page: int | None) -> QuestionSource:
    """由试卷分类信息拼出题目出处；分类修改后随之更新。"""
    m = meta or {}
    title = m.get("title") or ""
    grade_type = f"{m.get('grade') or ''}{m.get('paperType') or m.get('paper_type') or ''}"
    parts = [m.get("schoolYear") or m.get("school_year") or "", m.get("region") or "", grade_type]
    label = " · ".join(p for p in parts if p)
    name = title or (file_name or "").rsplit(".", 1)[0]
    if name:
        label = f"{label}《{name}》" if label else f"《{name}》"
    if no:
        label += f"第 {no} 题"
    return QuestionSource(
        title=title, file_name=file_name or "", school_year=m.get("schoolYear") or m.get("school_year") or "",
        region=m.get("region") or "", grade=m.get("grade") or "", paper_type=m.get("paperType") or m.get("paper_type") or "",
        subject=m.get("subject") or "", no=no, page=page, label=label,
    )


def question_out(q: DraftQuestion) -> DraftQuestionOut:
    out = DraftQuestionOut.model_validate(q)
    out.images = image_urls(q)
    s = object_session(q)
    job = s.get(ParseJob, q.job_id) if s else None
    if job:
        out.source = question_source(job.meta, job.file_name, q.no, q.page)
    return out


def list_questions(s: Session, job_id: str) -> list[DraftQuestion]:
    return list(s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id).order_by(DraftQuestion.no)))


def renumber(s: Session, job_id: str) -> list[DraftQuestion]:
    qs = list_questions(s, job_id)
    for i, q in enumerate(qs, 1):
        q.no = i
    s.flush()
    return qs


def image_urls(q: DraftQuestion) -> list[str]:
    store = get_store()
    return [store.public_url(k) for k in q.images]


def merge_with_previous(s: Session, q: DraftQuestion) -> None:
    prev = s.scalars(
        select(DraftQuestion).where(DraftQuestion.job_id == q.job_id, DraftQuestion.no < q.no)
        .order_by(DraftQuestion.no.desc()).limit(1)
    ).first()
    if prev is None:
        raise HTTPException(400, "第一题没有上一题可合并")
    score = prev.score + q.score
    kps = list(prev.knowledge_points)
    for k in q.knowledge_points:
        if all(p["id"] != k["id"] for p in kps):
            kps.append(k)
    prev.stem = prev.stem + "\n" + q.stem
    prev.options = prev.options or q.options
    prev.answer = "；".join(x for x in (prev.answer, q.answer) if x) or None
    prev.analysis = "\n".join(x for x in (prev.analysis, q.analysis) if x) or None
    # 合并后来源不一致时视为人工处理过；只有一方有答案时沿用那一方的来源
    sources = {x.answer_source for x in (prev, q) if x.answer}
    prev.answer_source = sources.pop() if len(sources) == 1 else ("manual" if sources else None)
    prev.answer_note = "；".join(x for x in (prev.answer_note, q.answer_note) if x) or None
    prev.coef = round((prev.coef * prev.score + q.coef * q.score) / score, 2) if score else prev.coef
    prev.score = score
    prev.knowledge_points = kps
    prev.confidence = min(prev.confidence, q.confidence)
    prev.block_ids = prev.block_ids + q.block_ids
    prev.regions = prev.regions + q.regions
    prev.images = prev.images + q.images
    prev.status = "draft"
    s.delete(q)
    s.flush()


# 小问标记：（1）(1) ⑴
SUB_MARK = re.compile(r"(?:（\d+）|\(\d+\)|[⑴-⒇])")


def _split_marks(text: str) -> tuple[str, list[str]] | None:
    marks = list(SUB_MARK.finditer(text))
    if len(marks) < 2:
        return None
    head = text[: marks[0].start()]
    ends = [m.start() for m in marks[1:]] + [len(text)]
    return head, [text[m.start():e].strip() for m, e in zip(marks, ends)]


def split_sub_questions(s: Session, q: DraftQuestion) -> None:
    parsed = _split_marks(q.stem)
    if parsed is None:
        raise HTTPException(400, "未识别到（1）（2）等小问标记，请先编辑题目")
    head, parts = parsed
    answers = _split_marks(q.answer)[1] if q.answer and _split_marks(q.answer) else None
    n = len(parts)
    each = int(q.score // n)
    # 为新题腾出题号：之后的题整体后移
    for later in s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == q.job_id, DraftQuestion.no > q.no)):
        later.no += n - 1
    for i, part in enumerate(parts):
        s.add(DraftQuestion(
            id="q" + uuid.uuid4().hex[:20], job_id=q.job_id, no=q.no + i, type=q.type,
            score=q.score - each * (n - 1) if i == n - 1 else each, page=q.page,
            stem=head + part, options=list(q.options),
            answer=answers[i] if answers and len(answers) == n else (q.answer if i == 0 else None),
            analysis=q.analysis if i == 0 else None, answer_source=q.answer_source, answer_note=q.answer_note,
            knowledge_points=list(q.knowledge_points), coef=q.coef,
            confidence=q.confidence, block_ids=list(q.block_ids), regions=list(q.regions), images=list(q.images),
            duplicate_of=None, status="draft",
        ))
    s.delete(q)
    s.flush()
