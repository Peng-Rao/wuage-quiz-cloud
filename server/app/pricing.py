"""模型单价与调用成本。

单价来源（按优先级）：LLM_PRICES / MINERU_PRICE_PER_PAGE 手动配置 > 百炼官方定价页 > OpenRouter 接口。
Worker 每 PRICE_REFRESH_HOURS 小时抓取一次，写入 model_price（价格变化时新增一条，历史调用仍按当时的单价）；
每次 AI 调用记录用量时按当时的单价计算定价成本，写入 ai_usage.cost。

    uv run python -m app.pricing --refresh    # 立即抓取单价
    uv run python -m app.pricing --show       # 当前单价
    uv run python -m app.pricing --backfill   # 为尚未计价的历史调用按当时的单价补算费用

计价规则（百炼）：按单次请求的输入 token 数落在哪个阶梯，整个请求按该阶梯单价计费；
分时定价的模型在北京时间 8:00–22:00 按高峰价、其余时间按闲时价；隐式缓存命中的输入 token 按输入单价的 20%。
"""

import argparse
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import AiUsage, AppMeta, ModelPrice, SessionLocal, utcnow

log = logging.getLogger(__name__)

ALIYUN_URL = "https://www.alibabacloud.com/help/en/model-studio/model-pricing"
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
CHECKED_KEY = "prices_checked_at"
BEIJING = timezone(timedelta(hours=8))
PEAK_HOURS = range(8, 22)  # 北京时间 8:00–22:00 为高峰
SYMBOLS = {"USD": "$", "CNY": "¥"}
CODES = {"$": "USD", "¥": "CNY", "元": "CNY"}


# ---------------- 百炼官方定价页 ----------------

class _Tables(HTMLParser):
    """收集页面中的表格及其所在的最近一个标题。"""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[dict[str, Any]] = []
        self._stack: list[dict[str, Any]] = []
        self._heading = ""
        self._in_heading: str | None = None
        self._heading_text = ""
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading, self._heading_text = tag, ""
        elif tag == "table":
            self._stack.append({"heading": self._heading, "rows": []})
        elif tag == "tr" and self._stack:
            self._stack[-1]["rows"].append([])
        elif tag in ("td", "th") and self._stack and self._stack[-1]["rows"]:
            self._cell = {"text": "", "rs": int(a.get("rowspan") or 1), "cs": int(a.get("colspan") or 1)}
        elif tag in ("br", "p", "div", "li") and self._cell is not None:
            self._cell["text"] += " "

    def handle_endtag(self, tag: str) -> None:
        if tag == self._in_heading:
            self._heading = re.sub(r"\s+", " ", self._heading_text).strip()
            self._in_heading = None
        elif tag in ("td", "th") and self._cell is not None and self._stack:
            self._cell["text"] = re.sub(r"\s+", " ", html.unescape(self._cell["text"])).strip()
            self._stack[-1]["rows"][-1].append(self._cell)
            self._cell = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._heading_text += data
        if self._cell is not None:
            self._cell["text"] += data


def _grid(rows: list[list[dict[str, Any]]]) -> list[list[str]]:
    """展开 rowspan / colspan，得到规整的二维表。"""
    out: list[list[str]] = []
    pending: dict[int, tuple[int, str]] = {}
    for row in rows:
        line: list[str] = []
        col = 0
        cells = iter(row)
        while True:
            if col in pending and pending[col][0] > 0:
                left, text = pending[col]
                line.append(text)
                pending[col] = (left - 1, text)
                col += 1
                continue
            c = next(cells, None)
            if c is None:
                break
            for _ in range(c["cs"]):
                line.append(c["text"])
                if c["rs"] > 1:
                    pending[col] = (c["rs"] - 1, c["text"])
                col += 1
        out.append(line)
    return out


def _price(cell: str) -> tuple[float | None, float | None]:
    """「$2」→ (2, None)；「Busy hours: $0.3 Idle hours: $0.15」→ (0.3, 0.15)。"""
    busy = re.search(r"Busy hours:\s*\$\s*([\d.]+)", cell)
    idle = re.search(r"Idle hours:\s*\$\s*([\d.]+)", cell)
    if busy:
        return float(busy.group(1)), float(idle.group(1)) if idle else None
    m = re.search(r"\$\s*([\d.]+)", cell)
    return (float(m.group(1)), None) if m else (None, None)


def _tier_max(cell: str) -> int | None:
    """「32K<Token≤256K」→ 256000；没有上限或无阶梯时为 None。"""
    m = re.search(r"≤\s*([\d.]+)\s*([KkMm]?)", cell)
    if not m:
        return None
    n = float(m.group(1)) * {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1)
    return int(n)


def _mode(cell: str) -> str | None:
    c = cell.lower()
    if "non-thinking and thinking" in c or "thinking and non-thinking" in c or not c:
        return None
    if "non-thinking" in c:
        return "non_thinking"
    if "thinking" in c:
        return "thinking"
    return None


def parse_aliyun(page: str, region: str, scope: str) -> dict[str, list[dict[str, Any]]]:
    """百炼定价页 → {模型: 阶梯单价}。只取指定地域标题下、部署范围一致的行（每百万 token，USD）。"""
    parser = _Tables()
    parser.feed(page)
    found: dict[str, dict[tuple, dict[str, Any]]] = {}
    for t in parser.tables:
        if region.lower() not in (t["heading"] or "").lower():
            continue
        grid = _grid(t["rows"])
        if len(grid) < 2:
            continue
        head = [h.lower() for h in grid[0]]

        def col(*keys: str) -> int | None:
            return next((i for i, h in enumerate(head) if any(k in h for k in keys)), None)

        c_model, c_scope = col("model"), col("deployment scope")
        c_in, c_out = col("input price"), col("output price")
        c_tier = col("input tokens per request")
        # 「Model ID」也含 mode，模式列按表头完全匹配
        c_mode = next((i for i, h in enumerate(head) if h.strip() == "mode"), None)
        if None in (c_model, c_in, c_out):
            continue
        for row in grid[1:]:
            if len(row) <= max(c_model, c_in, c_out):
                continue
            if c_scope is not None and row[c_scope].strip().lower() != scope.lower():
                continue
            model = (row[c_model].split() or [""])[0].strip().lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", model):
                continue
            inp, inp_off = _price(row[c_in])
            out, out_off = _price(row[c_out])
            if inp is None or out is None:
                continue
            tier = {"max_input": _tier_max(row[c_tier]) if c_tier is not None else None,
                    "input": inp, "output": out, "input_offpeak": inp_off, "output_offpeak": out_off,
                    "cached_input": None, "mode": _mode(row[c_mode]) if c_mode is not None else None}
            # 同一模型在页面中可能出现多次，保留第一次
            found.setdefault(model, {}).setdefault((tier["max_input"], tier["mode"]), tier)
    return {m: sorted(t.values(), key=_tier_sort) for m, t in found.items()}


def _tier_sort(t: dict[str, Any]) -> tuple:
    return (t.get("mode") or "", t["max_input"] is None, t["max_input"] or 0)


# ---------------- OpenRouter ----------------

def parse_openrouter(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """OpenRouter /models → {模型: 单价}。按 id 最后一段与模型名完全一致匹配，跳过 :batch 等变体（USD）。"""
    out: dict[str, list[dict[str, Any]]] = {}
    for m in data.get("data") or []:
        mid = str(m.get("id") or "")
        if ":" in mid:
            continue
        name = mid.split("/")[-1].lower()
        p = m.get("pricing") or {}
        try:
            inp, outp = float(p["prompt"]) * 1e6, float(p["completion"]) * 1e6
        except (KeyError, TypeError, ValueError):
            continue
        cached = p.get("input_cache_read")
        out.setdefault(name, [{
            "max_input": None, "input": round(inp, 6), "output": round(outp, 6), "input_offpeak": None,
            "output_offpeak": None, "cached_input": round(float(cached) * 1e6, 6) if cached else None, "mode": None,
        }])
    return out


# ---------------- 抓取并写入 ----------------

def fetch_sources(settings: Settings, client: httpx.Client | None = None) -> tuple[dict[str, dict], list[str]]:
    """抓取各在线来源，返回 {来源: {模型: 阶梯}} 与告警。单个来源失败不影响其他来源。"""
    client = client or httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    out: dict[str, dict] = {}
    warnings: list[str] = []
    if "aliyun" in settings.price_sources:
        try:
            r = client.get(ALIYUN_URL)
            r.raise_for_status()
            out["aliyun"] = parse_aliyun(r.text, settings.price_region, settings.price_scope)
            if not out["aliyun"]:
                warnings.append(f"百炼定价页未解析到「{settings.price_region} / {settings.price_scope}」的单价，页面可能已改版")
        except httpx.HTTPError as e:
            warnings.append(f"百炼定价页抓取失败：{e}")
    if "openrouter" in settings.price_sources:
        try:
            r = client.get(OPENROUTER_URL)
            r.raise_for_status()
            out["openrouter"] = parse_openrouter(r.json())
        except (httpx.HTTPError, ValueError) as e:
            warnings.append(f"OpenRouter 单价抓取失败：{e}")
    return out, warnings


def tracked_models(s: Session, settings: Settings) -> list[str]:
    """需要单价的模型：模型池中的模型与历史上调用过的模型。"""
    used = s.scalars(select(AiUsage.model).where(AiUsage.provider == "llm").distinct())
    return sorted(set(settings.text_models) | set(settings.vision_model_pool) | set(used))


def _spec(row: ModelPrice) -> tuple:
    return row.source, row.currency, row.tiers, row.cache_ratio


def _save(s: Session, provider: str, model: str, source: str, currency: str, tiers: list[dict[str, Any]],
          cache_ratio: float | None, notes: str | None, now: datetime) -> str:
    latest = s.scalars(select(ModelPrice).where(ModelPrice.provider == provider, ModelPrice.model == model)
                       .order_by(ModelPrice.fetched_at.desc(), ModelPrice.id.desc()).limit(1)).first()
    if latest is not None and _spec(latest) == (source, currency, tiers, cache_ratio):
        latest.checked_at = now
        return "unchanged"
    s.add(ModelPrice(provider=provider, model=model, source=source, currency=currency, tiers=tiers,
                     cache_ratio=cache_ratio, notes=notes, fetched_at=now, checked_at=now))
    return "new" if latest is None else "changed"


def refresh(settings: Settings | None = None, client: httpx.Client | None = None,
            now: datetime | None = None) -> list[str]:
    """抓取单价并写入 model_price，返回每个模型的结果说明。"""
    settings = settings or get_settings()
    now = now or utcnow()
    fetched, warnings = fetch_sources(settings, client)
    lines = list(warnings)
    manual_currency = CODES.get(settings.currency, settings.currency)
    with SessionLocal() as s:
        for model in tracked_models(s, settings):
            key = model.lower()
            ratio = settings.llm_model_cache_ratio.get(model, settings.llm_cache_hit_ratio)
            manual = settings.llm_prices.get(model)
            if manual and "input" in manual and "output" in manual:
                tiers = [{"max_input": None, "input": manual["input"], "output": manual["output"], "input_offpeak": None,
                          "output_offpeak": None, "cached_input": manual.get("cached_input"), "mode": None}]
                source, currency, notes = "manual", manual_currency, "LLM_PRICES"
            elif key in fetched.get("aliyun", {}):
                tiers, source, currency = fetched["aliyun"][key], "aliyun", "USD"
                notes = f"{settings.price_region} / {settings.price_scope}"
            elif key in fetched.get("openrouter", {}):
                tiers, source, currency, notes = fetched["openrouter"][key], "openrouter", "USD", "OpenRouter 列表价"
            else:
                lines.append(f"{model}：未找到单价")
                continue
            result = _save(s, "llm", model, source, currency, tiers, ratio, notes, now)
            first = tiers[0]
            lines.append(f"{model}：{source} {currency} 输入 {first['input']} / 输出 {first['output']}"
                         + (f"（{len(tiers)} 档）" if len(tiers) > 1 else "") + f" [{result}]")
        if settings.mineru_price_per_page is not None:
            result = _save(s, "mineru", "mineru", "manual", manual_currency,
                           [{"per_page": settings.mineru_price_per_page}], None, "MINERU_PRICE_PER_PAGE", now)
            lines.append(f"MinerU：{settings.mineru_price_per_page} {manual_currency}/页 [{result}]")
        meta = s.get(AppMeta, CHECKED_KEY)
        if meta is None:
            s.add(AppMeta(key=CHECKED_KEY, value=now.isoformat()))
        else:
            meta.value = now.isoformat()
        s.commit()
    return lines


def claim_refresh(settings: Settings, now: datetime | None = None) -> bool:
    """到了抓取时间则占用本次抓取（多个 Worker 只有一个执行），返回是否应抓取。"""
    if settings.price_refresh_hours <= 0:
        return False
    now = now or utcnow()
    with SessionLocal() as s:
        meta = s.scalars(select(AppMeta).where(AppMeta.key == CHECKED_KEY).with_for_update()).first()
        if meta is not None:
            try:
                last = datetime.fromisoformat(meta.value)
            except ValueError:
                last = None
            if last and now - last < timedelta(hours=settings.price_refresh_hours):
                return False
            meta.value = now.isoformat()
        else:
            s.add(AppMeta(key=CHECKED_KEY, value=now.isoformat()))
        s.commit()
    return True


# ---------------- 计价 ----------------

def price_at(s: Session, provider: str, model: str, at: datetime) -> ModelPrice | None:
    """调用时有效的单价：该时间之前最近的一条；早于第一条记录的调用按第一条计。"""
    base = select(ModelPrice).where(ModelPrice.provider == provider, ModelPrice.model == model)
    row = s.scalars(base.where(ModelPrice.fetched_at <= at)
                    .order_by(ModelPrice.fetched_at.desc(), ModelPrice.id.desc()).limit(1)).first()
    return row or s.scalars(base.order_by(ModelPrice.fetched_at, ModelPrice.id).limit(1)).first()


def _pick_tier(tiers: list[dict[str, Any]], prompt_tokens: int, thinking: bool) -> dict[str, Any] | None:
    mode = "thinking" if thinking else "non_thinking"
    pool = [t for t in tiers if t.get("mode") == mode] or [t for t in tiers if t.get("mode") is None] or tiers
    bounded = sorted((t for t in pool if t.get("max_input") is not None), key=lambda t: t["max_input"])
    for t in bounded:
        if prompt_tokens <= t["max_input"]:
            return t
    unbounded = [t for t in pool if t.get("max_input") is None]
    return unbounded[0] if unbounded else (bounded[-1] if bounded else None)


def cost_for(price: ModelPrice, *, provider: str, prompt_tokens: int = 0, completion_tokens: int = 0,
             reasoning_tokens: int = 0, cached_tokens: int = 0, pages: int = 0,
             at: datetime | None = None) -> float | None:
    """按单价计算一次调用的定价成本（单价货币）。"""
    if provider == "mineru":
        per_page = (price.tiers or [{}])[0].get("per_page")
        return None if per_page is None else round(pages * per_page, 8)
    tier = _pick_tier(price.tiers or [], prompt_tokens, thinking=reasoning_tokens > 0)
    if tier is None:
        return None
    at = at or utcnow()
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    peak = at.astimezone(BEIJING).hour in PEAK_HOURS
    inp = tier["input"] if peak or tier.get("input_offpeak") is None else tier["input_offpeak"]
    out = tier["output"] if peak or tier.get("output_offpeak") is None else tier["output_offpeak"]
    cached = min(cached_tokens, prompt_tokens)
    cached_price = tier.get("cached_input")
    if cached_price is None:
        cached_price = inp * (price.cache_ratio if price.cache_ratio is not None else 1.0)
    return round(((prompt_tokens - cached) * inp + cached * cached_price + completion_tokens * out) / 1_000_000, 8)


def price_usage(s: Session, u: AiUsage) -> None:
    """为一条用量记录写入成本（找不到单价时保持为空）。"""
    model = "mineru" if u.provider == "mineru" else u.model
    at = u.created_at or utcnow()
    price = price_at(s, u.provider, model, at)
    if price is None:
        return
    u.cost = cost_for(price, provider=u.provider, prompt_tokens=u.prompt_tokens, completion_tokens=u.completion_tokens,
                      reasoning_tokens=u.reasoning_tokens, cached_tokens=u.cached_tokens, pages=u.pages, at=at)
    u.currency = price.currency
    u.price_id = price.id


def backfill(only_missing: bool = True) -> tuple[int, int]:
    """为历史调用按当时的单价补算费用，返回 (已计价, 仍无单价)。"""
    priced = missing = 0
    with SessionLocal() as s:
        q = select(AiUsage)
        if only_missing:
            q = q.where(AiUsage.cost.is_(None))
        for u in s.scalars(q.order_by(AiUsage.id)):
            price_usage(s, u)
            if u.cost is None:
                missing += 1
            else:
                priced += 1
        s.commit()
    return priced, missing


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="立即抓取单价")
    ap.add_argument("--show", action="store_true", help="显示当前单价")
    ap.add_argument("--backfill", action="store_true", help="为尚未计价的历史调用补算费用")
    ap.add_argument("--all", action="store_true", help="与 --backfill 同用：全部重新计价")
    args = ap.parse_args()
    if args.refresh:
        for line in refresh():
            print(line)
    if args.backfill:
        priced, missing = backfill(only_missing=not args.all)
        print(f"已计价 {priced} 条，仍无单价 {missing} 条")
    if args.show or not (args.refresh or args.backfill):
        with SessionLocal() as s:
            latest = select(ModelPrice.model, func.max(ModelPrice.id).label("id")).group_by(ModelPrice.model).subquery()
            for p in s.scalars(select(ModelPrice).join(latest, ModelPrice.id == latest.c.id).order_by(ModelPrice.model)):
                tiers = "；".join(
                    (f"≤{t['max_input']:,}: " if t.get("max_input") else "")
                    + (f"{t['per_page']}/页" if "per_page" in t else f"{t['input']} / {t['output']}")
                    + (f"（闲时 {t['input_offpeak']} / {t['output_offpeak']}）" if t.get("input_offpeak") else "")
                    + (f" [{t['mode']}]" if t.get("mode") else "") for t in p.tiers)
                print(f"{p.model:28} {p.source:10} {p.currency} {tiers}  （{p.fetched_at:%Y-%m-%d} 起，"
                      f"{p.checked_at:%Y-%m-%d %H:%M} 核对）")


if __name__ == "__main__":
    main()
