"""管理员成本分析：按日期区间汇总 AI 调用的费用，并按用途、模型、账号、学科、时段与试卷拆分。

- 日期按北京时间划分（与峰谷计价一致），区间含首尾两天。
- 金额只统计主货币（与 summarize 一致：多种货币时取金额最大的），其余货币或缺单价的调用计入 unpriced_calls。
- 早期调用没有记录账号、学科时，按所属解析任务的上传者与学科补齐。
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import AiUsage, DraftQuestion, ParseJob, User
from .pricing import BEIJING, CODES, SOURCE_PEAK, billing_source, is_peak
from .schemas import CostAnalysis, CostBreakdown, CostDay, CostHour, CostJob
from .usage import cost_of, currency_of, job_subject, summarize

TOP_JOBS = 10


def _utc(d: date) -> datetime:
    return datetime.combine(d, time(), BEIJING).astimezone(timezone.utc)


def _local(at: datetime) -> datetime:
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(BEIJING)


def _rows(s: Session, school_id: str, start: date, end: date) -> list[AiUsage]:
    return list(s.scalars(select(AiUsage).where(
        AiUsage.school_id == school_id, AiUsage.created_at >= _utc(start),
        AiUsage.created_at < _utc(end + timedelta(days=1)),
    ).order_by(AiUsage.id)))


def _r(v: float) -> float:
    return round(v, 6)


def analyze(s: Session, school_id: str, start: date, end: date, settings: Settings | None = None) -> CostAnalysis:
    settings = settings or get_settings()
    days = (end - start).days + 1
    rows = _rows(s, school_id, start, end)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    prev_rows = _rows(s, school_id, prev_start, prev_end)

    summary = summarize(rows, settings)
    main = CODES.get(summary.currency, summary.currency)
    cost = {r.id: (c if (c := cost_of(r, settings)) is not None and currency_of(r, settings) == main else None)
            for r in rows}

    def total(rs: list[AiUsage]) -> float:
        return sum(cost[r.id] or 0.0 for r in rs)

    job_ids = {r.job_id for r in rows if r.job_id}
    jobs = {j.id: j for j in s.scalars(select(ParseJob).where(ParseJob.id.in_(job_ids)))} if job_ids else {}
    questions = dict(s.execute(
        select(DraftQuestion.job_id, func.count(DraftQuestion.id))
        .where(DraftQuestion.job_id.in_(job_ids)).group_by(DraftQuestion.job_id)
    ).all()) if job_ids else {}

    # 早期调用未记录账号 / 学科：按解析任务补齐
    def user_of(r: AiUsage) -> str | None:
        job = jobs.get(r.job_id) if r.job_id else None
        return r.user_id or (job.owner_id if job else None)

    def subject_of(r: AiUsage) -> str | None:
        return r.subject or job_subject(jobs.get(r.job_id) if r.job_id else None)

    user_ids = {u for r in rows if (u := user_of(r))}
    names = {u.id: u.display_name for u in s.scalars(select(User).where(User.id.in_(user_ids)))} if user_ids else {}

    # ---- 日趋势（补齐没有调用的日期）----
    by_day: dict[str, list[AiUsage]] = {(start + timedelta(days=i)).isoformat(): [] for i in range(days)}
    for r in rows:
        by_day.setdefault(_local(r.created_at).date().isoformat(), []).append(r)
    daily = []
    for day, rs in sorted(by_day.items()):
        llm = [r for r in rs if r.provider == "llm"]
        mineru = [r for r in rs if r.provider == "mineru"]
        daily.append(CostDay(
            date=day, calls=len(rs), jobs=len({r.job_id for r in rs if r.job_id}),
            total_tokens=sum(r.prompt_tokens + r.completion_tokens for r in llm),
            pages=sum(r.pages for r in mineru), llm_cost=_r(total(llm)), mineru_cost=_r(total(mineru)),
            cost=_r(total(rs)),
        ))

    # ---- 时段分布（北京时间）与峰谷 ----
    source = billing_source(settings)
    peak = SOURCE_PEAK.get(source)
    hours: list[list[AiUsage]] = [[] for _ in range(24)]
    for r in rows:
        hours[_local(r.created_at).hour].append(r)
    hourly = [CostHour(hour=h, calls=len(rs), cost=_r(total(rs)),
                       peak=bool(peak) and any(lo <= h < hi for lo, hi in peak["ranges"]))
              for h, rs in enumerate(hours)]
    all_cost = total(rows)
    peak_cost_share = None
    if peak and all_cost > 0:
        peak_cost_share = round(sum(cost[r.id] or 0.0 for r in rows if r.provider == "llm"
                                    and is_peak(_local(r.created_at), peak)) / all_cost, 4)

    # ---- 分组 ----
    def group(key: Callable[[AiUsage], str], label: Callable[[str], str],
              provider: Callable[[AiUsage], str | None] = lambda _: None) -> list[CostBreakdown]:
        groups: dict[str, list[AiUsage]] = {}
        for r in rows:
            groups.setdefault(key(r), []).append(r)
        out = []
        for k, rs in groups.items():
            llm = [r for r in rs if r.provider == "llm"]
            out.append(CostBreakdown(
                key=k, label=label(k), provider=provider(rs[0]), calls=len(rs),
                errors=sum(1 for r in rs if r.status != "ok"),
                prompt_tokens=sum(r.prompt_tokens for r in llm), completion_tokens=sum(r.completion_tokens for r in llm),
                cached_tokens=sum(r.cached_tokens for r in llm), pages=sum(r.pages for r in rs if r.provider == "mineru"),
                duration_ms=sum(r.duration_ms for r in rs), jobs=len({r.job_id for r in rs if r.job_id}),
                cost=_r(total(rs)), unpriced_calls=sum(1 for r in rs if cost[r.id] is None),
            ))
        return sorted(out, key=lambda b: (-b.cost, -b.calls, b.key))

    by_purpose = group(lambda r: r.purpose, lambda k: k)
    by_model = group(lambda r: r.model, lambda k: k, lambda r: r.provider)
    by_user = group(lambda r: user_of(r) or "", lambda k: names.get(k, "已删除账号") if k else "未关联账号")
    by_subject = group(lambda r: subject_of(r) or "", lambda k: k or "未识别学科")

    # ---- 费用最高的试卷 ----
    per_job: dict[str, list[AiUsage]] = {}
    for r in rows:
        if r.job_id in jobs:
            per_job.setdefault(r.job_id, []).append(r)
    ranked = sorted(per_job.items(), key=lambda kv: (-total(kv[1]), -len(kv[1])))[:TOP_JOBS]
    top_jobs = []
    for jid, rs in ranked:
        job = jobs[jid]
        top_jobs.append(CostJob(
            id=jid, file_name=job.file_name, status=job.status,
            owner=names.get(job.owner_id) if job.owner_id else None, subject=job_subject(job),
            pages=job.page_count, questions=questions.get(jid, 0), calls=len(rs),
            total_tokens=sum(r.prompt_tokens + r.completion_tokens for r in rs if r.provider == "llm"),
            cost=_r(total(rs)), created_at=job.created_at,
        ))

    # ---- 单位成本：区间内完成的解析任务，其在区间内的调用费用 ÷ 份数 / 页数 / 题数（组卷等不计入）----
    done = [j for j in jobs.values() if j.status == "done"]
    done_cost = sum(total(per_job.get(j.id, [])) for j in done)
    pages = sum(j.page_count or 0 for j in done)
    qcount = sum(questions.get(j.id, 0) for j in done)
    priced_any = summary.cost is not None

    def per(n: int) -> float | None:
        return _r(done_cost / n) if priced_any and n else None

    prev = summarize(prev_rows, settings) if prev_rows else None
    previous_cost = prev.cost if prev and prev.currency == summary.currency else None
    avg_daily = _r(all_cost / days) if priced_any else None
    llm_prompt = summary.prompt_tokens

    return CostAnalysis(
        start=start.isoformat(), end=end.isoformat(), days=days, summary=summary,
        previous_cost=previous_cost, previous_start=prev_start.isoformat(), previous_end=prev_end.isoformat(),
        avg_daily_cost=avg_daily, projected_monthly_cost=_r(avg_daily * 30) if avg_daily is not None else None,
        jobs=len(done), pages=pages, questions=qcount,
        cost_per_job=per(len(done)), cost_per_page=per(pages), cost_per_question=per(qcount),
        error_cost=_r(sum(cost[r.id] or 0.0 for r in rows if r.status != "ok")),
        cache_hit_rate=round(summary.cached_tokens / llm_prompt, 4) if llm_prompt else None,
        peak_cost_share=peak_cost_share, billing_source=source,
        daily=daily, hourly=hourly, by_purpose=by_purpose, by_model=by_model, by_user=by_user,
        by_subject=by_subject, top_jobs=top_jobs,
    )


def today() -> date:
    return datetime.now(BEIJING).date()
