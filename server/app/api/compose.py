from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import compose_sessions as sessions
from ..auth import require_subject
from ..compose import compose
from ..config import get_settings
from ..db import get_session, utcnow
from ..schemas import (
    ComposeModels, ComposeRequest, ComposeResult, ComposeSessionDetail, ComposeSessionItem, ComposeSessionRename,
)
from ..services import current_school
from ..usage import attribute

router = APIRouter()


@router.get("/api/compose/models", response_model=ComposeModels)
def list_compose_models() -> ComposeModels:
    settings = get_settings()
    return ComposeModels(default_model=settings.llm_model if settings.llm_enabled else None,
                         models=settings.compose_model_ids)


@router.post("/api/compose", response_model=ComposeResult)
async def compose_paper(req: ComposeRequest, s: Session = Depends(get_session)) -> ComposeResult:
    """AI 组卷：按老师描述的学生情况与要求，从校本题库中选题并赋分。多轮修改时传入完整对话，整份试卷重新生成。
    结果保存为老师本人的组卷记录：传入 sessionId 时追加到该记录，否则新建，返回的 sessionId 用于继续对话。

    仅管理员和组长可用（见 main.py）；组长只能为本人学科组卷，题目范围由会话的数据权限限定。"""
    user = s.info["user"]
    require_subject(user, req.subject)
    if req.session_id:
        sessions.own(s, user, req.session_id)  # 先校验，避免白白调用大模型
    attribute(user.id, req.subject)
    try:
        result = await compose(s, current_school(), req)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    session_id = sessions.save_turn(s, user, current_school(), req, result)
    s.commit()
    return result.model_copy(update={"session_id": session_id})


@router.get("/api/compose/sessions", response_model=list[ComposeSessionItem])
def list_compose_sessions(stage: str | None = Query(None, max_length=16), subject: str | None = Query(None, max_length=16),
                          limit: int = Query(50, ge=1, le=200),
                          s: Session = Depends(get_session)) -> list[ComposeSessionItem]:
    """本人的组卷记录，最近使用的在前；可按学段学科筛选。"""
    return sessions.list_sessions(s, s.info["user"], current_school(), stage, subject, limit)


@router.get("/api/compose/sessions/{session_id}", response_model=ComposeSessionDetail)
def read_compose_session(session_id: str, s: Session = Depends(get_session)) -> ComposeSessionDetail:
    return sessions.detail(s, s.info["user"], session_id)


@router.patch("/api/compose/sessions/{session_id}", response_model=ComposeSessionItem)
def rename_compose_session(session_id: str, body: ComposeSessionRename,
                           s: Session = Depends(get_session)) -> ComposeSessionItem:
    user = s.info["user"]
    row = sessions.own(s, user, session_id)
    row.title = body.title.strip() or row.title
    row.updated_at = utcnow()
    s.commit()
    return sessions.item(row)


@router.delete("/api/compose/sessions/{session_id}", status_code=204)
def delete_compose_session(session_id: str, s: Session = Depends(get_session)) -> None:
    s.delete(sessions.own(s, s.info["user"], session_id))
    s.commit()
