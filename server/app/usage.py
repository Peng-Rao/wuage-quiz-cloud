"""AI 用量统计与成本估算。

- 每次大模型 / MinerU 调用写一行 ai_usage；记录失败只打日志，绝不影响解析流程。
- 费用不落库，查询时按当前配置的单价计算：之后补填或调整单价，历史数据随之重算。
- 通过 contextvar 关联当前解析任务，调用方无需层层传递 job_id。
"""

import logging
import math
import re
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import AiUsage, DraftQuestion, ParseJob, SessionLocal, utcnow
from .schemas import DailyUsage, UsageCall, UsageOverview, UsageSummary

log = logging.getLogger(__name__)

current_job: ContextVar[str | None] = ContextVar("current_job", default=None)

_CJK = re.compile(r"[　-〿一-鿿＀-￯]")


def estimate_tokens(text: str) -> int:
    """粗略估算：中文约 1 字 1 token，其余约 4 字符 1 token。仅在服务端未返回用量时使用。"""
    cjk = len(_CJK.findall(text))
    return cjk + math.ceil((len(text) - cjk) / 4)


def record(
    provider: str, purpose: str, model: str, *, prompt_tokens: int = 0, completion_tokens: int = 0,
    reasoning_tokens: int = 0, cached_tokens: int = 0, pages: int = 0, duration_ms: int = 0,
    estimated: bool = False, status: str = "ok", job_id: str | None = None,
) -> None:
    job_id = job_id or current_job.get()
    try:
        with SessionLocal() as s:
            job = s.get(ParseJob, job_id) if job_id else None
            u = AiUsage(
                job_id=job_id, school_id=job.school_id if job else "demo", provider=provider, purpose=purpose,
                model=model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                reasoning_tokens=reasoning_tokens, cached_tokens=cached_tokens, pages=pages,
                duration_ms=duration_ms, estimated=estimated, status=status, created_at=utcnow(),
            )
            try:
                # 按调用时的单价计算定价成本；计价失败不影响记录用量
                from .pricing import price_usage

                price_usage(s, u)
            except Exception:  # noqa: BLE001
                log.exception("计算 AI 调用成本失败")
            s.add(u)
            s.commit()
    except Exception:  # noqa: BLE001
        log.exception("记录 AI 用量失败")


def cost_of(u: AiUsage, settings: Settings) -> float | None:
    """单次调用的费用：记录时按当时单价算好的成本（ai_usage.cost）；早期没有记录成本的调用按配置单价估算。"""
    if u.cost is not None:
        return u.cost
    return _config_cost(u, settings)


def currency_of(u: AiUsage, settings: Settings) -> str:
    """成本的货币代码：已记录的按单价货币，按配置单价估算的为 CURRENCY。"""
    if u.cost is not None and u.currency:
        return u.currency
    from .pricing import CODES

    return CODES.get(settings.currency, settings.currency)


def _config_cost(u: AiUsage, settings: Settings) -> float | None:
    if u.provider == "mineru":
        return None if settings.mineru_price_per_page is None else u.pages * settings.mineru_price_per_page
    price = settings.llm_prices.get(u.model)
    if not price or "input" not in price or "output" not in price:
        return None
    cached = min(u.cached_tokens, u.prompt_tokens)
    cached_price = price.get("cached_input", price["input"])
    return ((u.prompt_tokens - cached) * price["input"] + cached * cached_price
            + u.completion_tokens * price["output"]) / 1_000_000


def summarize(rows: Iterable[AiUsage], settings: Settings | None = None) -> UsageSummary:
    settings = settings or get_settings()
    rows = list(rows)
    llm = [r for r in rows if r.provider == "llm"]
    from .pricing import SYMBOLS

    costs = [(r, cost_of(r, settings)) for r in rows]
    by_currency: dict[str, float] = {}
    for r, c in costs:
        if c is not None:
            code = currency_of(r, settings)
            by_currency[code] = by_currency.get(code, 0.0) + c
    # 多种货币时以金额最大的为准，其他货币的成本列入 costs_by_currency、不计入 cost
    main = max(by_currency, key=by_currency.get) if by_currency else _default_currency(settings)
    costs = [(r, c if c is not None and currency_of(r, settings) == main else None) for r, c in costs]
    unpriced = sorted({(r.model if r.provider == "llm" else "MinerU") for r, c in costs if c is None})
    llm_cost = sum(c for r, c in costs if c is not None and r.provider == "llm")
    mineru_cost = sum(c for r, c in costs if c is not None and r.provider == "mineru")
    any_priced = any(c is not None for _, c in costs)
    return UsageSummary(
        calls=len(rows),
        llm_calls=len(llm),
        errors=sum(1 for r in rows if r.status != "ok"),
        prompt_tokens=sum(r.prompt_tokens for r in llm),
        completion_tokens=sum(r.completion_tokens for r in llm),
        reasoning_tokens=sum(r.reasoning_tokens for r in llm),
        cached_tokens=sum(r.cached_tokens for r in llm),
        total_tokens=sum(r.prompt_tokens + r.completion_tokens for r in llm),
        pages=sum(r.pages for r in rows if r.provider == "mineru"),
        duration_ms=sum(r.duration_ms for r in rows),
        estimated=any(r.estimated for r in rows),
        cost=round(llm_cost + mineru_cost, 6) if any_priced else None,
        llm_cost=round(llm_cost, 6) if any(c is not None and r.provider == "llm" for r, c in costs) else None,
        mineru_cost=round(mineru_cost, 6) if any(c is not None and r.provider == "mineru" for r, c in costs) else None,
        priced=not unpriced,
        unpriced_models=unpriced,
        currency=SYMBOLS.get(main, main),
        costs_by_currency={k: round(v, 6) for k, v in by_currency.items()},
    )


def _default_currency(settings: Settings) -> str:
    from .pricing import CODES

    return CODES.get(settings.currency, settings.currency)


def job_rows(s: Session, job_id: str) -> list[AiUsage]:
    return list(s.scalars(select(AiUsage).where(AiUsage.job_id == job_id).order_by(AiUsage.id)))


def calls_out(rows: list[AiUsage], settings: Settings | None = None) -> list[UsageCall]:
    settings = settings or get_settings()
    return [
        UsageCall.model_validate(r).model_copy(update={"cost": cost_of(r, settings), "currency": currency_of(r, settings)})
        for r in rows
    ]


def overview(s: Session, school_id: str, days: int) -> UsageOverview:
    """近 N 天用量，并给出每份试卷 / 每页 / 每题的平均成本，用于估算后续费用。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = list(s.scalars(select(AiUsage).where(AiUsage.school_id == school_id, AiUsage.created_at >= since)))
    summary = summarize(rows)
    job_ids = {r.job_id for r in rows if r.job_id}
    jobs = list(s.scalars(select(ParseJob).where(ParseJob.id.in_(job_ids)))) if job_ids else []
    done = [j for j in jobs if j.status == "done"]
    pages = sum(j.page_count or 0 for j in done)
    questions = len(list(s.scalars(select(DraftQuestion.id).where(DraftQuestion.job_id.in_([j.id for j in done]))))) if done else 0

    by_day: dict[str, list[AiUsage]] = {}
    for r in rows:
        created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)
        by_day.setdefault(created.astimezone().date().isoformat(), []).append(r)
    daily = []
    for day, rs in sorted(by_day.items()):
        sm = summarize(rs)
        daily.append(DailyUsage(date=day, jobs=len({r.job_id for r in rs if r.job_id}),
                                total_tokens=sm.total_tokens, pages=sm.pages, cost=sm.cost))

    def per(n: int) -> float | None:
        return round(summary.cost / n, 6) if summary.cost is not None and n else None

    return UsageOverview(
        days=days, jobs=len(done), pages=pages, questions=questions, summary=summary,
        cost_per_job=per(len(done)), cost_per_page=per(pages), cost_per_question=per(questions),
        tokens_per_job=round(summary.total_tokens / len(done)) if done else None,
        daily=daily,
    )
