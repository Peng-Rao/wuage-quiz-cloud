"""管理后台：AI 用量与成本统计（与题库「AI 成本」页相同）、模型单价的抓取、手动设置与重新计价。"""

from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select

from ... import pricing
from ...config import get_settings
from ...console import audit, require
from ...db import AppMeta, ConsoleAccount, ModelPrice, SessionLocal
from ...schemas import CostAnalysis, Model, ModelPriceOut, UsageOverview
from ...services import current_school
from ...usage import overview
from ..usage import model_prices

router = APIRouter(prefix="/api/admin")
can_view = require("ai")


# ---------------- 用量统计 ----------------

@router.get("/usage/summary", response_model=UsageOverview)
def usage_summary(days: int = Query(30, ge=1, le=365), _: ConsoleAccount = Depends(can_view)) -> UsageOverview:
    with SessionLocal() as s:
        return overview(s, current_school(), days)


@router.get("/usage/analysis", response_model=CostAnalysis)
def usage_analysis(start: date | None = None, end: date | None = None,
                   _: ConsoleAccount = Depends(can_view)) -> CostAnalysis:
    from ...cost_analysis import analyze, today

    end = end or today()
    start = start or end - timedelta(days=29)
    if start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if (end - start).days >= 366:
        raise HTTPException(422, "日期区间不能超过 366 天")
    with SessionLocal() as s:
        return analyze(s, current_school(), start, end)


# ---------------- 单价 ----------------

@router.get("/prices", response_model=list[ModelPriceOut])
def prices(history: bool = False, _: ConsoleAccount = Depends(can_view)) -> list[ModelPriceOut]:
    with SessionLocal() as s:
        return model_prices(history, s)


@router.get("/prices/status")
def price_status(_: ConsoleAccount = Depends(can_view)) -> dict[str, Any]:
    st = get_settings()
    with SessionLocal() as s:
        checked = s.get(AppMeta, pricing.CHECKED_KEY)
        models = pricing.tracked_models(s, st)
    return {"billingSource": pricing.billing_source(st), "costSources": pricing.cost_sources(st),
            "sources": st.price_sources, "refreshHours": st.price_refresh_hours,
            "checkedAt": checked.value if checked else None, "models": models}


@router.post("/prices/refresh")
def refresh_prices(actor: ConsoleAccount = Depends(can_view)) -> dict[str, Any]:
    """立即从各来源抓取单价（腾讯云 TokenHub、百炼、OpenRouter），价格变化时新增记录。"""
    lines = pricing.refresh()
    with SessionLocal() as s:
        audit(s, actor, "prices.refresh", None, {"lines": len(lines)})
        s.commit()
    return {"lines": lines}


class Tier(BaseModel):
    """单价阶梯，单位为每百万 token；max_input 为该阶梯的输入 token 上限（空为不限）。"""

    max_input: int | None = Field(None, gt=0)
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    input_offpeak: float | None = Field(None, ge=0)
    output_offpeak: float | None = Field(None, ge=0)
    cached_input: float | None = Field(None, ge=0)
    cached_input_offpeak: float | None = Field(None, ge=0)
    mode: Literal["thinking", "non_thinking"] | None = None


class Peak(BaseModel):
    ranges: list[tuple[int, int]] = Field(min_length=1, max_length=6)
    weekdays_only: bool = False

    @model_validator(mode="after")
    def valid(self):
        if any(not (0 <= lo < hi <= 24) for lo, hi in self.ranges):
            raise ValueError("高峰时段须在 0–24 点之间，且开始早于结束")
        return self


class PriceWrite(Model):
    provider: Literal["llm", "mineru"]
    model: str = Field(min_length=1, max_length=64)
    currency: Literal["CNY", "USD"]
    tiers: list[Tier] = Field(default_factory=list, max_length=10)
    per_page: float | None = Field(None, ge=0)
    cache_ratio: float | None = Field(None, ge=0, le=1)
    peak: Peak | None = None
    notes: str = Field("", max_length=200)

    @model_validator(mode="after")
    def by_provider(self):
        self.model = self.model.strip()
        if not self.model:
            raise ValueError("请填写模型名称")
        if self.provider == "mineru":
            if self.per_page is None:
                raise ValueError("请填写每页单价")
            self.model = "mineru"
        elif not self.tiers:
            raise ValueError("请至少填写一档单价")
        return self


class Recompute(Model):
    # 重新计算该时间之后的全部调用；为空时只为尚未计价的调用补算
    since: datetime | None = None
    all: bool = False


@router.post("/prices", response_model=ModelPriceOut)
def set_price(body: PriceWrite, actor: ConsoleAccount = Depends(can_view)) -> ModelPriceOut:
    """设置手动单价：优先于抓取的单价用于计算之后调用的成本；需要时可「重新计价」历史调用。"""
    tiers = ([{"per_page": body.per_page}] if body.provider == "mineru"
             else [t.model_dump() for t in sorted(body.tiers, key=lambda t: (t.max_input is None, t.max_input or 0))])
    notes = f"{pricing.CONSOLE_NOTE} · {actor.email}" + (f" · {body.notes.strip()}" if body.notes.strip() else "")
    result = pricing.save_manual(body.provider, body.model, body.currency, tiers,
                                 None if body.provider == "mineru" else body.cache_ratio,
                                 body.peak.model_dump() if body.peak else None, notes)
    with SessionLocal() as s:
        audit(s, actor, "prices.set", f"{body.provider}/{body.model}", {"currency": body.currency, "tiers": tiers,
                                                                       "result": result})
        s.commit()
        row = s.scalars(select(ModelPrice).where(ModelPrice.provider == body.provider, ModelPrice.model == body.model,
                                                 ModelPrice.source == "manual")
                        .order_by(ModelPrice.fetched_at.desc(), ModelPrice.id.desc()).limit(1)).one()
        return ModelPriceOut.model_validate(row).model_copy(update={"billing": True})


@router.delete("/prices/manual", status_code=204)
def clear_manual(provider: Literal["llm", "mineru"], model: str = Query(min_length=1, max_length=64),
                 actor: ConsoleAccount = Depends(can_view)) -> None:
    """删除该模型的全部手动单价，恢复按计费来源的抓取单价计价（历史调用需重新计价才会改变）。"""
    with SessionLocal() as s:
        n = s.execute(delete(ModelPrice).where(ModelPrice.provider == provider, ModelPrice.model == model,
                                               ModelPrice.source == "manual")).rowcount or 0
        if not n:
            raise HTTPException(404, "该模型没有手动单价")
        audit(s, actor, "prices.clear_manual", f"{provider}/{model}", {"rows": n})
        s.commit()


@router.post("/prices/recompute")
def recompute(body: Recompute, actor: ConsoleAccount = Depends(can_view)) -> dict[str, int]:
    """按调用时的单价重新计算历史调用的成本（调整单价、更换计费来源后使用）。"""
    priced, missing = pricing.backfill(only_missing=not body.all and body.since is None, since=body.since)
    with SessionLocal() as s:
        audit(s, actor, "prices.recompute", None, {"since": body.since.isoformat() if body.since else None,
                                                    "all": body.all, "priced": priced, "missing": missing})
        s.commit()
    return {"priced": priced, "missing": missing}
