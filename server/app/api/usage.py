from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import ModelPrice, get_session
from ..schemas import CostAnalysis, ModelPriceOut, UsageOverview
from ..services import current_school
from ..usage import overview

router = APIRouter(prefix="/api/usage")


@router.get("/summary", response_model=UsageOverview)
def usage_summary(days: int = Query(30, ge=1, le=365), s: Session = Depends(get_session)) -> UsageOverview:
    """近 N 天 AI 用量与平均成本（每份试卷 / 每页 / 每题），用于估算后续费用。"""
    return overview(s, current_school(), days)


@router.get("/analysis", response_model=CostAnalysis)
def usage_analysis(start: date | None = None, end: date | None = None,
                   s: Session = Depends(get_session)) -> CostAnalysis:
    """成本分析：start、end 为北京时间日期（含首尾），默认近 30 天；与上一个等长周期对比。"""
    from ..cost_analysis import analyze, today

    end = end or today()
    start = start or end - timedelta(days=29)
    if start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if (end - start).days >= 366:
        raise HTTPException(422, "日期区间不能超过 366 天")
    return analyze(s, current_school(), start, end)


@router.get("/prices", response_model=list[ModelPriceOut])
def model_prices(history: bool = False, s: Session = Depends(get_session)) -> list[ModelPriceOut]:
    """模型单价：默认每个模型、每个来源的当前单价，history=true 时含历史记录（价格变化时新增）。
    billing 为 true 的单价用于计算成本（手动配置或当前调用平台），其余来源作为参考。"""
    from ..config import get_settings
    from ..pricing import cost_sources

    used = set(cost_sources(get_settings()))
    q = select(ModelPrice)
    if not history:
        latest = (select(func.max(ModelPrice.id).label("id"))
                  .group_by(ModelPrice.provider, ModelPrice.model, ModelPrice.source).subquery())
        q = q.where(ModelPrice.id.in_(select(latest.c.id)))
    rows = s.scalars(q.order_by(ModelPrice.model, ModelPrice.source, ModelPrice.fetched_at))
    return [ModelPriceOut.model_validate(p).model_copy(update={"billing": p.source in used or p.provider == "mineru"})
            for p in rows]
