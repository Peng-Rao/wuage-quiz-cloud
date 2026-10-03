from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from ..knowledge_tree import resolve_kps
from ..schemas import DraftQuestionOut, DraftQuestionPatch, SourceImage
from ..services import (
    get_question, merge_with_previous, question_out, renumber, split_sub_questions,
)
from ..storage import get_store

router = APIRouter(prefix="/api/draft-questions")

# 修改这些字段视为人工核对
_REVIEW_FIELDS = {"stem", "material", "options", "answer", "type"}


@router.patch("/{qid}", response_model=DraftQuestionOut)
def update_question(qid: str, patch: DraftQuestionPatch, s: Session = Depends(get_session)) -> DraftQuestionOut:
    q = get_question(s, qid)
    fields = patch.model_dump(exclude_unset=True)
    stem = fields.get("stem", q.stem) or ""
    material = fields.get("material", q.material) or ""
    if not stem.strip() and not material.strip():
        raise HTTPException(422, "题干与阅读材料不能都为空")
    # 只处理实际变化的字段（编辑弹窗会带上全部字段）
    changed = {k for k, v in fields.items() if getattr(q, k) != v}
    if "knowledge_points" in fields:
        # 知识点 id 统一按「学科 + 名称」生成，手动输入与 AI 标注的同名知识点一致
        fields["knowledge_points"] = resolve_kps(s, q.job_id, fields["knowledge_points"])
    for k, v in fields.items():
        setattr(q, k, v)
    if _REVIEW_FIELDS & changed:
        q.confidence = 1.0
    if "coef" in changed:
        q.difficulty_source = "manual"
    if {"answer", "analysis"} & changed:
        # 老师改过的答案不再视为 AI 生成
        q.answer_source = "manual" if q.answer else None
        q.answer_note = None
    q.status = "draft"
    s.commit()
    return question_out(q)


@router.post("/{qid}/merge-previous", response_model=list[DraftQuestionOut])
def merge_previous(qid: str, s: Session = Depends(get_session)) -> list[DraftQuestionOut]:
    q = get_question(s, qid)
    job_id = q.job_id
    merge_with_previous(s, q)
    qs = renumber(s, job_id)
    s.commit()
    return [question_out(x) for x in qs]


@router.post("/{qid}/split", response_model=list[DraftQuestionOut])
def split(qid: str, s: Session = Depends(get_session)) -> list[DraftQuestionOut]:
    q = get_question(s, qid)
    job_id = q.job_id
    split_sub_questions(s, q)
    qs = renumber(s, job_id)
    s.commit()
    return [question_out(x) for x in qs]


@router.get("/{qid}/source", response_model=list[SourceImage])
def source(qid: str, s: Session = Depends(get_session)) -> list[SourceImage]:
    q = get_question(s, qid)
    store = get_store()
    pages = sorted({r["page"] for r in q.regions})
    out = []
    for p in pages:
        key = f"jobs/{q.job_id}/pages/{p}.png"
        if store.exists(key):
            out.append(SourceImage(page=p, url=store.public_url(key), regions=[r for r in q.regions if r["page"] == p]))
    return out
