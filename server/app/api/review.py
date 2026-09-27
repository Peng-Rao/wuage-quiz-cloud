from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_subject, staff
from ..db import BankQuestion, User, get_session, utcnow
from ..schemas import Model

router = APIRouter(dependencies=[Depends(staff)])


class Review(Model):
    question_ids: list[str] = Field(min_length=1, max_length=100)
    owner_id: str
    approved: bool


@router.post("/api/bank/review")
def review(body: Review, s: Session = Depends(get_session)):
    actor = s.info["user"]
    recipient = s.get(User, body.owner_id)
    if not recipient or not recipient.active:
        raise HTTPException(400, "请选择有效用户")
    if actor.role == "leader" and recipient.role != "member":
        raise HTTPException(403, "组长只能向普通用户分配题目")
    ids = set(body.question_ids)
    rows = list(s.scalars(select(BankQuestion).where(BankQuestion.id.in_(ids))))
    if len(rows) != len(ids):
        raise HTTPException(404, "部分题目不存在或无权访问")
    for q in rows:
        subject = (q.meta or {}).get("subject", "")
        require_subject(actor, subject)
        require_subject(recipient, subject)
        q.owner_id = recipient.id
        q.reviewed_by = actor.id if body.approved else None
        q.reviewed_at = utcnow() if body.approved else None
    s.commit()
    return {"updated": len(rows)}

