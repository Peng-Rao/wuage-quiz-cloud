"""模型单价与调用成本。

单价来源：腾讯云 TokenHub 模型价格文档、百炼官方定价页、OpenRouter 接口，以及 LLM_PRICES / MINERU_PRICE_PER_PAGE
手动配置。每 PRICE_REFRESH_HOURS 小时抓取一次，各来源分别写入 model_price（价格变化时新增一条）。
每次 AI 调用记录用量时，按手动单价或「计费来源」（当前调用平台，见 PRICE_BILLING_SOURCE）当时的单价计算定价成本，
写入 ai_usage.cost；其他来源的单价只作参考。

    uv run python -m app.pricing --refresh    # 立即抓取单价
    uv run python -m app.pricing --show       # 当前单价
    uv run python -m app.pricing --backfill   # 为尚未计价的历史调用按当时的单价补算费用
    uv run python -m app.pricing --backfill --since 2026-10-02T08:40:00Z   # 重新计算该时间之后的调用

计价规则：按单次请求的输入 token 数落在哪个阶梯，整个请求按该阶梯单价计费；分时定价按请求时间（北京时间）
落在高峰还是空闲时段（TokenHub：工作日 9–12、14–18 点为高峰，周末全天空闲；百炼：每天 8–22 点为高峰）；
缓存命中的输入 token 按来源给出的缓存单价，未给出时按输入单价的 LLM_CACHE_HIT_RATIO。
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
TENCENT_DOC_URL = "https://cloud.tencent.com/document/product/1823/130055"
# 各来源的峰谷时段（北京时间，[开始, 结束) 小时）
ALIYUN_PEAK = {"ranges": [[8, 22]], "weekdays_only": False}
TENCENT_PEAK = {"ranges": [[9, 12], [14, 18]], "weekdays_only": True}
SOURCE_PEAK = {"aliyun": ALIYUN_PEAK, "tencent": TENCENT_PEAK}
CURRENCY_OF = {"tencent": "CNY", "aliyun": "USD", "openrouter": "USD"}
TENCENT_REGIONS = ("广州", "新加坡", "上海", "北京", "南京", "成都", "重庆", "香港", "硅谷", "弗吉尼亚", "法兰克福", "东京")
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
                    "cached_input": None, "cached_input_offpeak": None,
                    "mode": _mode(row[c_mode]) if c_mode is not None else None}
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
            "output_offpeak": None, "cached_input": round(float(cached) * 1e6, 6) if cached else None,
            "cached_input_offpeak": None, "mode": None,
        }])
    return out


# ---------------- 腾讯云 TokenHub ----------------

def _norm_name(s: str) -> str:
    """模型名归一：去掉「原厂直供」「正式版」、空格与连字符，小写。"""
    s = re.sub(r"原厂直供|正式版", "", s)
    return re.sub(r"[\s\-_/]+", "", s).lower()


def _tencent_tier_max(cell: str) -> int | None:
    """「输入长度（0, 128k]」→ 128000；「输入长度 32k+」或「-」→ None。"""
    m = re.search(r",\s*([\d.]+)\s*([kKmM])\s*\]", cell)
    if not m:
        return None
    return int(float(m.group(1)) * {"k": 1_000, "m": 1_000_000}[m.group(2).lower()])


def _num(cell: str) -> float | None:
    m = re.search(r"[\d.]+", cell.replace(",", ""))
    return float(m.group(0)) if m else None


def parse_tencent(page: str, region: str) -> dict[str, list[dict[str, Any]]]:
    """TokenHub 模型价格文档 → {文档中的模型名: 阶梯单价}（元 / 百万 tokens）。
    语言模型价格按地域分页签（广州、新加坡…），页签顺序与其后的价格表顺序一致；高峰 / 空闲两行合并为一个阶梯。"""
    parser = _Tables()
    parser.feed(page)
    tables = []
    for t in parser.tables:
        grid = _grid(t["rows"])
        if len(grid) < 2:
            continue
        head = [h.replace(" ", "") for h in grid[0]]
        if not any("推理输入" in h for h in head) or not any("推理输出" in h for h in head):
            continue
        if not any(h.startswith("模型名称") for h in head) or "批量" in (t["heading"] or ""):
            continue
        tables.append((t["heading"], grid))
    if not tables:
        return {}
    # 页签：「按 Token 计费（后付费）」标题之后、第一张表之前出现的地域名
    text = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    start = text.find(tables[0][0]) if tables[0][0] else -1
    seg = text[start:start + 2000] if start >= 0 else ""
    labels = [w.strip() for w in seg.split("\n") if w.strip() in TENCENT_REGIONS]
    labels = list(dict.fromkeys(labels))
    same = [g for h, g in tables if h == tables[0][0]]
    idx = labels.index(region) if region in labels else 0
    if idx >= len(same):
        return {}
    grid = same[idx]
    head = [h.replace(" ", "") for h in grid[0]]

    def col(key: str) -> int | None:
        return next((i for i, h in enumerate(head) if key in h), None)

    c_name, c_cond, c_peak = 0, col("条件"), col("峰谷")
    c_in, c_out, c_cache = col("推理输入"), col("推理输出"), col("缓存命中")
    found: dict[str, dict[tuple, dict[str, Any]]] = {}
    for row in grid[1:]:
        name = row[c_name].strip()
        inp, out = _num(row[c_in]), _num(row[c_out])
        if not name or inp is None or out is None:
            continue
        cache = _num(row[c_cache]) if c_cache is not None else None
        max_input = _tencent_tier_max(row[c_cond]) if c_cond is not None else None
        tier = found.setdefault(name, {}).setdefault((max_input,), {
            "max_input": max_input, "input": inp, "output": out, "input_offpeak": None, "output_offpeak": None,
            "cached_input": cache, "cached_input_offpeak": None, "mode": None})
        peak = row[c_peak] if c_peak is not None else ""
        if "空闲" in peak:
            tier["input_offpeak"], tier["output_offpeak"], tier["cached_input_offpeak"] = inp, out, cache
        elif "高峰" in peak:
            tier["input"], tier["output"], tier["cached_input"] = inp, out, cache
    for tiers in found.values():
        for tier in tiers.values():
            # 只有空闲价的行（不应出现）按空闲价计
            if tier["input_offpeak"] is not None and tier["input"] == tier["input_offpeak"] and tier["output"] == tier["output_offpeak"]:
                tier["input_offpeak"] = tier["output_offpeak"] = tier["cached_input_offpeak"] = None
    return {n: sorted(t.values(), key=_tier_sort) for n, t in found.items()}


def match_tencent(doc: dict[str, list[dict[str, Any]]], model: str, names: dict[str, str]) -> list[dict[str, Any]] | None:
    """API 模型 id → 文档中的单价。优先用 /v1/models 返回的显示名；带厂商前缀的 id（deepseek/…）对应「原厂直供」。"""
    direct = "/" in model
    wanted = names.get(model) or model.split("/")[-1]
    key = _norm_name(wanted)
    hits = [n for n in doc if _norm_name(n) == key]
    if not hits:
        return None
    pick = next((n for n in hits if ("原厂直供" in n) == direct), hits[0])
    return doc[pick]


# ---------------- 抓取并写入 ----------------

def fetch_sources(settings: Settings, client: httpx.Client | None = None) -> tuple[dict[str, dict], list[str]]:
    """抓取各在线来源，返回 {来源: {模型: 阶梯}} 与告警。单个来源失败不影响其他来源。"""
    client = client or httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    out: dict[str, dict] = {}
    warnings: list[str] = []
    if "tencent" in settings.price_sources:
        try:
            r = client.get(TENCENT_DOC_URL)
            r.raise_for_status()
            doc = parse_tencent(r.text, settings.price_tencent_region)
            if not doc:
                warnings.append(f"TokenHub 价格文档未解析到「{settings.price_tencent_region}」的语言模型单价，页面可能已改版")
            out["tencent"] = {"_doc": doc, "_names": _tokenhub_names(settings, client)}
        except httpx.HTTPError as e:
            warnings.append(f"TokenHub 价格文档抓取失败：{e}")
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


def _tokenhub_names(settings: Settings, client: httpx.Client) -> dict[str, str]:
    """TokenHub /v1/models：模型 id → 显示名（与价格文档中的名称对应）。未配置 TokenHub 时为空，按 id 推断。"""
    if not (billing_source(settings) == "tencent" and settings.llm_api_key):
        return {}
    try:
        r = client.get(settings.llm_base_url.rstrip("/") + "/models",
                       headers={"Authorization": f"Bearer {settings.llm_api_key}"})
        r.raise_for_status()
        return {str(m.get("id")): str(m.get("name") or "").strip() for m in r.json().get("data") or []}
    except (httpx.HTTPError, ValueError):
        return {}


def billing_source(settings: Settings) -> str:
    """计算成本所用的来源：与当前调用的平台一致。"""
    if settings.price_billing_source != "auto":
        return settings.price_billing_source
    host = httpx.URL(settings.llm_base_url).host if settings.llm_base_url else ""
    if host.endswith("tencentmaas.com"):
        return "tencent"
    if "aliyuncs.com" in host or "dashscope" in host:
        return "aliyun"
    return "openrouter"


def cost_sources(settings: Settings) -> list[str]:
    """计算成本时依次查找的单价来源：手动配置优先，其次计费来源。"""
    return ["manual", billing_source(settings)]


def tracked_models(s: Session, settings: Settings) -> list[str]:
    """需要单价的模型：当前配置的文字 / 看图模型与历史上调用过的模型。"""
    used = s.scalars(select(AiUsage.model).where(AiUsage.provider == "llm").distinct())
    return sorted({m for m in (settings.llm_model, settings.vision_model) if m} | set(used))


def _spec(row: ModelPrice) -> tuple:
    return row.currency, row.tiers, row.cache_ratio, row.peak


def _save(s: Session, provider: str, model: str, source: str, currency: str, tiers: list[dict[str, Any]],
          cache_ratio: float | None, notes: str | None, now: datetime, peak: dict[str, Any] | None = None) -> str:
    """同一模型、同一来源的单价历史：变化时新增一条，未变化只更新核对时间。"""
    latest = s.scalars(select(ModelPrice).where(ModelPrice.provider == provider, ModelPrice.model == model,
                                                ModelPrice.source == source)
                       .order_by(ModelPrice.fetched_at.desc(), ModelPrice.id.desc()).limit(1)).first()
    if latest is not None and _spec(latest) == (currency, tiers, cache_ratio, peak):
        latest.checked_at = now
        return "unchanged"
    s.add(ModelPrice(provider=provider, model=model, source=source, currency=currency, tiers=tiers,
                     cache_ratio=cache_ratio, peak=peak, notes=notes, fetched_at=now, checked_at=now))
    return "new" if latest is None else "changed"


def refresh(settings: Settings | None = None, client: httpx.Client | None = None,
            now: datetime | None = None) -> list[str]:
    """抓取各来源单价并写入 model_price，返回每个模型、每个来源的结果说明（* 为计费来源）。"""
    settings = settings or get_settings()
    now = now or utcnow()
    fetched, warnings = fetch_sources(settings, client)
    lines = list(warnings)
    manual_currency = CODES.get(settings.currency, settings.currency)
    billing = billing_source(settings)
    with SessionLocal() as s:
        for model in tracked_models(s, settings):
            ratio = settings.llm_model_cache_ratio.get(model, settings.llm_cache_hit_ratio)
            per_source: list[tuple[str, list[dict[str, Any]], str, str | None]] = []
            manual = settings.llm_prices.get(model)
            if manual and "input" in manual and "output" in manual:
                per_source.append(("manual", [{
                    "max_input": None, "input": manual["input"], "output": manual["output"], "input_offpeak": None,
                    "output_offpeak": None, "cached_input": manual.get("cached_input"), "cached_input_offpeak": None,
                    "mode": None}], manual_currency, "LLM_PRICES"))
            if "tencent" in fetched:
                tiers = match_tencent(fetched["tencent"]["_doc"], model, fetched["tencent"]["_names"])
                if tiers:
                    per_source.append(("tencent", tiers, "CNY", f"TokenHub {settings.price_tencent_region}"))
            if model.lower() in fetched.get("aliyun", {}):
                per_source.append(("aliyun", fetched["aliyun"][model.lower()], "USD",
                                   f"百炼 {settings.price_region} / {settings.price_scope}"))
            if model.lower() in fetched.get("openrouter", {}):
                per_source.append(("openrouter", fetched["openrouter"][model.lower()], "USD", "OpenRouter 列表价"))
            if not per_source:
                lines.append(f"{model}：未找到单价")
                continue
            for source, tiers, currency, notes in per_source:
                result = _save(s, "llm", model, source, currency, tiers, ratio, notes, now, SOURCE_PEAK.get(source))
                first = tiers[0]
                mark = "*" if source in ("manual", billing) else " "
                lines.append(f"{mark}{model}：{source} {currency} 输入 {first['input']} / 输出 {first['output']}"
                             + (f"（{len(tiers)} 档）" if len(tiers) > 1 else "")
                             + (f"（闲时 {first['input_offpeak']} / {first['output_offpeak']}）" if first.get("input_offpeak") else "")
                             + f" [{result}]")
        if settings.mineru_price_per_page is not None:
            result = _save(s, "mineru", "mineru", "manual", manual_currency,
                           [{"per_page": settings.mineru_price_per_page}], None, "MINERU_PRICE_PER_PAGE", now)
            lines.append(f"*MinerU：{settings.mineru_price_per_page} {manual_currency}/页 [{result}]")
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

def price_at(s: Session, provider: str, model: str, at: datetime,
             sources: list[str] | None = None) -> ModelPrice | None:
    """调用时有效的单价：按来源优先级，取该来源在该时间之前最近的一条；早于第一条记录的调用按第一条计。
    sources 为空时不限来源（取最近的一条）。"""
    for source in sources or [None]:
        base = select(ModelPrice).where(ModelPrice.provider == provider, ModelPrice.model == model)
        if source is not None:
            base = base.where(ModelPrice.source == source)
        row = s.scalars(base.where(ModelPrice.fetched_at <= at)
                        .order_by(ModelPrice.fetched_at.desc(), ModelPrice.id.desc()).limit(1)).first()
        row = row or s.scalars(base.order_by(ModelPrice.fetched_at, ModelPrice.id).limit(1)).first()
        if row is not None:
            return row
    return None


def is_peak(at: datetime, peak: dict[str, Any] | None) -> bool:
    """请求时间（北京时间）是否处于高峰时段；不分峰谷时视为高峰（按标准价）。"""
    if not peak:
        return True
    local = at.astimezone(BEIJING)
    if peak.get("weekdays_only") and local.weekday() >= 5:
        return False
    hour = local.hour + local.minute / 60
    return any(lo <= hour < hi for lo, hi in peak.get("ranges") or [])


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
    peak = is_peak(at, price.peak)
    inp = tier["input"] if peak or tier.get("input_offpeak") is None else tier["input_offpeak"]
    out = tier["output"] if peak or tier.get("output_offpeak") is None else tier["output_offpeak"]
    cached = min(cached_tokens, prompt_tokens)
    cached_price = tier.get("cached_input") if peak or tier.get("cached_input_offpeak") is None \
        else tier["cached_input_offpeak"]
    if cached_price is None:
        cached_price = inp * (price.cache_ratio if price.cache_ratio is not None else 1.0)
    return round(((prompt_tokens - cached) * inp + cached * cached_price + completion_tokens * out) / 1_000_000, 8)


def price_usage(s: Session, u: AiUsage, settings: Settings | None = None) -> None:
    """为一条用量记录写入成本：按手动单价或计费来源当时的单价（找不到时保持为空）。"""
    settings = settings or get_settings()
    model = "mineru" if u.provider == "mineru" else u.model
    at = u.created_at or utcnow()
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    sources = ["manual"] if u.provider == "mineru" else cost_sources(settings)
    price = price_at(s, u.provider, model, at, sources)
    if price is None:
        u.cost = u.currency = u.price_id = None
        return
    u.cost = cost_for(price, provider=u.provider, prompt_tokens=u.prompt_tokens, completion_tokens=u.completion_tokens,
                      reasoning_tokens=u.reasoning_tokens, cached_tokens=u.cached_tokens, pages=u.pages, at=at)
    u.currency = price.currency
    u.price_id = price.id


def backfill(only_missing: bool = True, since: datetime | None = None) -> tuple[int, int]:
    """为历史调用按当时的单价补算费用，返回 (已计价, 仍无单价)。since 给出时重新计算该时间之后的全部调用
    （如更换调用平台后，按新的计费来源重算）。"""
    priced = missing = 0
    with SessionLocal() as s:
        q = select(AiUsage)
        if since is not None:
            q = q.where(AiUsage.created_at >= since)
        elif only_missing:
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
    ap.add_argument("--since", type=datetime.fromisoformat, help="与 --backfill 同用：重新计算该时间之后的调用")
    args = ap.parse_args()
    if args.refresh:
        for line in refresh():
            print(line)
    if args.backfill:
        priced, missing = backfill(only_missing=not args.all, since=args.since)
        print(f"已计价 {priced} 条，仍无单价 {missing} 条")
    if args.show or not (args.refresh or args.backfill):
        used = cost_sources(get_settings())
        print(f"计费来源：{' > '.join(used)}（* 标记的单价用于计算成本，其余为参考）")
        with SessionLocal() as s:
            latest = (select(func.max(ModelPrice.id).label("id"))
                      .group_by(ModelPrice.provider, ModelPrice.model, ModelPrice.source).subquery())
            rows = s.scalars(select(ModelPrice).where(ModelPrice.id.in_(select(latest.c.id)))
                             .order_by(ModelPrice.model, ModelPrice.source))
            for p in rows:
                tiers = "；".join(
                    (f"≤{t['max_input']:,}: " if t.get("max_input") else "")
                    + (f"{t['per_page']}/页" if "per_page" in t else f"{t['input']} / {t['output']}")
                    + (f"（闲时 {t['input_offpeak']} / {t['output_offpeak']}）" if t.get("input_offpeak") else "")
                    + (f" [{t['mode']}]" if t.get("mode") else "") for t in p.tiers)
                mark = "*" if p.source in used or p.provider == "mineru" else " "
                print(f"{mark} {p.model:38} {p.source:10} {p.currency} {tiers}  （{p.fetched_at:%Y-%m-%d} 起，"
                      f"{p.checked_at:%Y-%m-%d %H:%M} 核对）")


if __name__ == "__main__":
    main()
