from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import ModelPrice, get_session
from ..schemas import ModelPriceOut, UsageOverview
from ..services import current_school
from ..usage import overview

router = APIRouter(prefix="/api/usage")


@router.get("/summary", response_model=UsageOverview)
def usage_summary(days: int = Query(30, ge=1, le=365), s: Session = Depends(get_session)) -> UsageOverview:
    """近 N 天 AI 用量与平均成本（每份试卷 / 每页 / 每题），用于估算后续费用。"""
    return overview(s, current_school(), days)


@router.get("/prices", response_model=list[ModelPriceOut])
def model_prices(history: bool = False, s: Session = Depends(get_session)) -> list[ModelPriceOut]:
    """模型单价：默认每个模型的当前单价，history=true 时含历史记录（价格变化时新增）。"""
    q = select(ModelPrice)
    if not history:
        latest = select(func.max(ModelPrice.id).label("id")).group_by(ModelPrice.provider, ModelPrice.model).subquery()
        q = q.where(ModelPrice.id.in_(select(latest.c.id)))
    return [ModelPriceOut.model_validate(p) for p in s.scalars(q.order_by(ModelPrice.model, ModelPrice.fetched_at))]
