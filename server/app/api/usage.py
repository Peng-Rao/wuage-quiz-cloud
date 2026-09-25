from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import UsageOverview
from ..services import current_school
from ..usage import overview

router = APIRouter(prefix="/api/usage")


@router.get("/summary", response_model=UsageOverview)
def usage_summary(days: int = Query(30, ge=1, le=365), s: Session = Depends(get_session)) -> UsageOverview:
    """近 N 天 AI 用量与平均成本（每份试卷 / 每页 / 每题），用于估算后续费用。"""
    return overview(s, current_school(), days)
