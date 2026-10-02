from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require_subject
from ..compose import compose
from ..db import get_session
from ..schemas import ComposeRequest, ComposeResult
from ..services import current_school
from ..usage import attribute

router = APIRouter()


@router.post("/api/compose", response_model=ComposeResult)
async def compose_paper(req: ComposeRequest, s: Session = Depends(get_session)) -> ComposeResult:
    """AI 组卷：按老师描述的学生情况与要求，从校本题库中选题并赋分。多轮修改时传入完整对话，整份试卷重新生成。

    仅管理员和组长可用（见 main.py）；组长只能为本人学科组卷，题目范围由会话的数据权限限定。"""
    require_subject(s.info["user"], req.subject)
    attribute(s.info["user"].id, req.subject)
    try:
        return await compose(s, current_school(), req)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
