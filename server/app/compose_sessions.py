"""AI 组卷记录：每位老师各自保存，可继续对话、重新打开或删除。

- 每次组卷成功后写入：新对话新建一条记录，继续对话时追加本次的要求与 AI 的回复。
- 试卷只保存题目编号与分值；打开记录时按老师当前的数据权限重新读取题目，被删除或无权查看的题不再显示并给出提示。
"""

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from .bank import bank_out
from .db import BankQuestion, ComposeSession, User, utcnow
from .schemas import (
    ComposeItem, ComposeMessage, ComposeRequest, ComposeResult, ComposeSection, ComposeSessionDetail,
    ComposeSessionItem,
)

# 每条记录最多保存的消息数（老师与 AI 各算一条），超出时丢弃最早的
MAX_MESSAGES = 200
PREVIEW_CHARS = 60


def _compact(r: ComposeResult) -> dict[str, Any]:
    data = r.model_dump(mode="json", exclude={"sections", "session_id"})
    data["sections"] = [{"type": sec.type, "score": sec.score,
                         "items": [{"id": it.question.id, "score": it.score} for it in sec.items]}
                        for sec in r.sections]
    return data


def _question_count(result: dict[str, Any] | None) -> int:
    return sum(len(sec.get("items") or []) for sec in (result or {}).get("sections") or [])


def own(s: Session, user: User, session_id: str) -> ComposeSession:
    row = s.get(ComposeSession, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "组卷记录不存在")
    return row


def save_turn(s: Session, user: User, school_id: str, req: ComposeRequest, result: ComposeResult) -> str:
    """保存本次组卷（调用方提交事务），返回记录编号。"""
    now = utcnow()
    reply = {"role": "assistant", "content": result.reply}
    if req.session_id:
        row = own(s, user, req.session_id)
        if (row.stage, row.subject) != (req.stage, req.subject):
            raise HTTPException(400, "该组卷记录属于其他学段学科，请新建组卷")
        messages = [*(row.messages or []), req.messages[-1].model_dump(), reply]
    else:
        row = ComposeSession(id=uuid.uuid4().hex, school_id=school_id, user_id=user.id, stage=req.stage,
                             subject=req.subject, created_at=now)
        s.add(row)
        messages = [*(m.model_dump() for m in req.messages), reply]
    row.messages = messages[-MAX_MESSAGES:]
    row.title = result.title[:128]
    row.model = result.model or req.model
    row.total = result.total or req.total
    row.difficulty = result.difficulty
    row.result = _compact(result)
    row.updated_at = now
    return row.id


def item(row: ComposeSession) -> ComposeSessionItem:
    asks = [m.get("content", "") for m in row.messages or [] if m.get("role") == "user"]
    last = asks[-1] if asks else ""
    return ComposeSessionItem(
        id=row.id, title=row.title, stage=row.stage, subject=row.subject, turns=len(asks),
        question_count=_question_count(row.result), total=row.total,
        preview=last[:PREVIEW_CHARS] + ("…" if len(last) > PREVIEW_CHARS else ""),
        created_at=row.created_at, updated_at=row.updated_at)


def list_sessions(s: Session, user: User, school_id: str, stage: str | None, subject: str | None,
                  limit: int) -> list[ComposeSessionItem]:
    q = select(ComposeSession).where(ComposeSession.user_id == user.id, ComposeSession.school_id == school_id)
    if stage:
        q = q.where(ComposeSession.stage == stage)
    if subject:
        q = q.where(ComposeSession.subject == subject)
    return [item(row) for row in s.scalars(q.order_by(ComposeSession.updated_at.desc()).limit(limit))]


def _hydrate(s: Session, data: dict[str, Any]) -> ComposeResult:
    """按当前数据权限读取题目（会话已按权限过滤 BankQuestion），缺失的题跳过并提示。"""
    ids = [it["id"] for sec in data.get("sections") or [] for it in sec.get("items") or []]
    found = {q.id: q for q in s.scalars(select(BankQuestion).where(BankQuestion.id.in_(ids))
                                        .options(defer(BankQuestion.embedding)))} if ids else {}
    sections = []
    for sec in data.get("sections") or []:
        items = [ComposeItem(question=bank_out(found[it["id"]]), score=it["score"])
                 for it in sec.get("items") or [] if it["id"] in found]
        if items:
            sections.append(ComposeSection(type=sec["type"], score=sum(i.score for i in items), items=items))
    gaps = list(data.get("gaps") or [])
    missing = len(ids) - sum(len(sec.items) for sec in sections)
    if missing:
        gaps.insert(0, f"有 {missing} 道题已从题库删除或您已无权查看，未显示；可继续对话重新组卷")
    fields = {k: v for k, v in data.items() if k not in ("sections", "gaps")}
    return ComposeResult(**fields, sections=sections, gaps=gaps)


def detail(s: Session, user: User, session_id: str) -> ComposeSessionDetail:
    row = own(s, user, session_id)
    result = _hydrate(s, row.result).model_copy(update={"session_id": row.id}) if row.result else None
    return ComposeSessionDetail(
        id=row.id, title=row.title, stage=row.stage, subject=row.subject, model=row.model, total=row.total,
        difficulty=row.difficulty, messages=[ComposeMessage.model_validate(m) for m in row.messages or []],
        result=result, created_at=row.created_at, updated_at=row.updated_at)
