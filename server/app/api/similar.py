from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import ParseJob, get_session
from ..schemas import SimilarQuestion, SimilarSearchRequest
from ..services import current_school, get_question, question_source
from ..similar import DUPLICATE_SCORE, Match, embed, question_text, search

router = APIRouter()


def _out(s: Session, m: Match) -> SimilarQuestion:
    job_id = m.row.job_id if m.kind == "draft" else m.row.source_job_id
    job = s.get(ParseJob, job_id) if job_id else None
    if m.kind == "bank":
        origin = question_source(m.row.meta, m.row.source_file_name or (job.file_name if job else None),
                                 m.row.source_no, m.row.source_page)
    else:
        origin = question_source(job.meta if job else None, job.file_name if job else None, m.row.no, m.row.page)
    return SimilarQuestion(
        id=m.id, source=m.kind, type=m.row.type, stem=m.row.stem, options=m.row.options, answer=m.row.answer,
        score=m.score, lexical=m.lexical, semantic=m.semantic, duplicate=m.score >= DUPLICATE_SCORE,
        job_id=job_id, file_name=job.file_name if job else None, origin=origin,
        knowledge_points=m.row.knowledge_points or [],
    )


@router.get("/api/draft-questions/{qid}/similar", response_model=list[SimilarQuestion])
async def similar_to_draft(
    qid: str, limit: int = Query(5, ge=1, le=20), scope: str = Query("all", pattern="^(bank|all)$"),
    s: Session = Depends(get_session),
) -> list[SimilarQuestion]:
    """与该草稿题相似的题：校本题库，以及（scope=all）其他试卷中尚未入库的题。"""
    q = get_question(s, qid)
    vector = q.embedding
    if vector is None:
        got = await embed([question_text(q.stem, q.options)], purpose="similar")
        vector = got[0] if got else None
    matches = search(s, current_school(), question_text(q.stem, q.options), vector=vector, qtype=q.type,
                     exclude_job=q.job_id, exclude_ids={q.id}, scope=scope, limit=limit)
    return [_out(s, m) for m in matches]


@router.post("/api/similar/search", response_model=list[SimilarQuestion])
async def search_similar(req: SimilarSearchRequest, s: Session = Depends(get_session)) -> list[SimilarQuestion]:
    """按文本搜索相似题（如录入一道题、拍照识别后查询）。"""
    got = await embed([req.text], purpose="similar")
    matches = search(s, current_school(), req.text, vector=got[0] if got else None, qtype=req.type,
                     scope=req.scope, limit=req.limit)
    return [_out(s, m) for m in matches]
