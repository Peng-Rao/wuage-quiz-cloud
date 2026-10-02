"""模型单价：解析百炼定价页 / OpenRouter、按阶梯与分时计价、写入价格历史与每次调用的成本。"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete, select

from app import pricing, usage
from app.config import Settings, get_settings
from app.db import AiUsage, AppMeta, ModelPrice, SessionLocal
from app.pricing import cost_for, parse_aliyun, parse_openrouter, price_at, refresh

from .test_api import client  # noqa: F401

PAGE = """<html><body>
<h4>China (Beijing)</h4>
<table><tr><th>Model ID</th><th>Input price (per 1 million tokens)</th><th>Output price (per 1 million tokens)</th></tr>
<tr><td>kimi-k3</td><td>$2.827</td><td>$14.133</td></tr></table>
<h4>Singapore</h4>
<table><tr><th>Model ID</th><th>Deployment scope</th><th>Input tokens per request</th>
<th>Input price (per 1 million tokens)</th><th>Output price (per 1 million tokens)</th><th>Free quota</th></tr>
<tr><td rowspan="3">qwen3.7-flash <p>Currently equivalent to qwen3.7-flash-2026-07-15</p></td>
<td rowspan="3">International</td><td>0&lt;Token≤32K</td><td>$0.030</td><td>$0.130</td><td rowspan="3">1 million tokens</td></tr>
<tr><td>32K&lt;Token≤256K</td><td>$0.100</td><td>$0.400</td></tr>
<tr><td>256K&lt;Token≤1M</td><td>$0.200</td><td>$0.800</td></tr>
<tr><td>qwen3.7-flash</td><td>Global</td><td>0&lt;Token≤1M</td><td>$9</td><td>$9</td><td>-</td></tr></table>
<table><tr><th>Model ID</th><th>Deployment scope</th><th>Input price (per 1 million tokens)</th><th>Output price (per 1 million tokens)</th></tr>
<tr><td>deepseek-v4.1-flash context caching</td><td>International</td>
<td>Busy hours: $0.3<br>Idle hours: $0.15</td><td>Busy hours: $1.2<br>Idle hours: $0.6</td></tr>
<tr><td>kimi-k3</td><td>International</td><td>$3</td><td>$15</td></tr></table>
<table><tr><th>Model ID</th><th>Deployment scope</th><th>Mode</th><th>Input price (per 1 million tokens)</th>
<th>Output price (per 1 million tokens)</th></tr>
<tr><td>qwen-plus</td><td>International</td><td>Non-thinking mode</td><td>$0.4</td><td>$1.2</td></tr>
<tr><td>qwen-plus</td><td>International</td><td>Thinking mode</td><td>$0.4</td><td>$4</td></tr></table>
</body></html>"""

OPENROUTER = {"data": [
    {"id": "z-ai/glm-5.3", "pricing": {"prompt": "0.0000014", "completion": "0.0000044", "input_cache_read": "0.00000028"}},
    {"id": "z-ai/glm-5.3-prime", "pricing": {"prompt": "0.0000028", "completion": "0.0000088"}},
    {"id": "moonshotai/kimi-k3:batch", "pricing": {"prompt": "0.000001", "completion": "0.000001"}},
]}


def test_parse_aliyun_region_scope_tiers_offpeak_and_mode():
    got = parse_aliyun(PAGE, "Singapore", "International")
    assert [(t["max_input"], t["input"], t["output"]) for t in got["qwen3.7-flash"]] == [
        (32_000, 0.03, 0.13), (256_000, 0.1, 0.4), (1_000_000, 0.2, 0.8)]  # Global 行不取
    assert got["deepseek-v4.1-flash"][0] | {} == {"max_input": None, "input": 0.3, "output": 1.2, "input_offpeak": 0.15,
                                                   "output_offpeak": 0.6, "cached_input": None, "mode": None}
    assert got["kimi-k3"][0]["input"] == 3  # 北京地域的 2.827 不取
    assert {t["mode"]: t["output"] for t in got["qwen-plus"]} == {"non_thinking": 1.2, "thinking": 4}


def test_parse_openrouter_exact_names_only():
    got = parse_openrouter(OPENROUTER)
    assert set(got) == {"glm-5.3", "glm-5.3-prime"}  # :batch 等变体跳过
    assert got["glm-5.3"][0] | {} == {"max_input": None, "input": 1.4, "output": 4.4, "input_offpeak": None,
                                      "output_offpeak": None, "cached_input": 0.28, "mode": None}


def price(tiers, ratio=0.2, provider="llm") -> ModelPrice:  # noqa: ANN001
    return ModelPrice(provider=provider, model="m", source="aliyun", currency="USD", tiers=tiers, cache_ratio=ratio)


BJ = timezone(timedelta(hours=8))


def test_cost_tiers_peak_cache_and_mode():
    flash = price(parse_aliyun(PAGE, "Singapore", "International")["qwen3.7-flash"])
    # 整个请求按输入 token 所在阶梯计价
    assert cost_for(flash, provider="llm", prompt_tokens=10_000, completion_tokens=1_000) == pytest.approx(
        (10_000 * 0.03 + 1_000 * 0.13) / 1e6)
    assert cost_for(flash, provider="llm", prompt_tokens=40_000, completion_tokens=1_000) == pytest.approx(
        (40_000 * 0.1 + 1_000 * 0.4) / 1e6)
    # 缓存命中按输入单价的 20%
    assert cost_for(flash, provider="llm", prompt_tokens=10_000, cached_tokens=4_000) == pytest.approx(
        (6_000 * 0.03 + 4_000 * 0.03 * 0.2) / 1e6)
    # 分时定价：北京时间 8:00–22:00 高峰价，其余闲时价
    ds = price(parse_aliyun(PAGE, "Singapore", "International")["deepseek-v4.1-flash"])
    peak = cost_for(ds, provider="llm", prompt_tokens=1_000_000, at=datetime(2026, 10, 2, 10, tzinfo=BJ))
    idle = cost_for(ds, provider="llm", prompt_tokens=1_000_000, at=datetime(2026, 10, 2, 23, tzinfo=BJ))
    assert (peak, idle) == (pytest.approx(0.3), pytest.approx(0.15))
    # 思考模式：有推理 token 时按思考模式单价
    plus = price(parse_aliyun(PAGE, "Singapore", "International")["qwen-plus"])
    assert cost_for(plus, provider="llm", completion_tokens=1_000_000) == pytest.approx(1.2)
    assert cost_for(plus, provider="llm", completion_tokens=1_000_000, reasoning_tokens=10) == pytest.approx(4)
    assert cost_for(price([{"per_page": 0.05}], None, "mineru"), provider="mineru", pages=20) == pytest.approx(1.0)


class Pages(httpx.BaseTransport):
    def __init__(self, page: str = PAGE, openrouter: dict | None = None):
        self.page, self.openrouter = page, openrouter or OPENROUTER

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if "openrouter" in request.url.host:
            return httpx.Response(200, json=self.openrouter)
        return httpx.Response(200, text=self.page)


@pytest.fixture
def clean_prices():
    with SessionLocal() as s:
        s.execute(delete(ModelPrice))
        s.execute(delete(AppMeta).where(AppMeta.key == pricing.CHECKED_KEY))
        s.commit()
    yield
    with SessionLocal() as s:
        s.execute(delete(ModelPrice))
        s.commit()


def settings(**kw) -> Settings:  # noqa: ANN003
    return get_settings().model_copy(update={"llm_models": ["qwen3.7-flash", "glm-5.3", "unknown-model"],
                                             "vision_models": [], "llm_prices": {}, **kw})


def test_refresh_saves_history_only_on_change(clean_prices):
    s = settings()
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    lines = refresh(s, httpx.Client(transport=Pages()), now=t0)
    assert any(line.startswith("unknown-model：未找到单价") for line in lines)
    with SessionLocal() as db:
        rows = {p.model: p for p in db.scalars(select(ModelPrice))}
    assert rows["qwen3.7-flash"].source == "aliyun" and len(rows["qwen3.7-flash"].tiers) == 3
    assert rows["glm-5.3"].source == "openrouter"  # 定价页上没有，用 OpenRouter 补充

    # 价格未变：只更新核对时间
    refresh(s, httpx.Client(transport=Pages()), now=t0 + timedelta(days=1))
    with SessionLocal() as db:
        assert db.scalar(select(ModelPrice).where(ModelPrice.model == "qwen3.7-flash")).checked_at == \
            t0 + timedelta(days=1)
        assert len(list(db.scalars(select(ModelPrice)))) == 2

    # 价格变化：新增一条，历史调用仍按当时的单价
    refresh(s, httpx.Client(transport=Pages(PAGE.replace("$0.030", "$0.050"))), now=t0 + timedelta(days=2))
    with SessionLocal() as db:
        assert price_at(db, "llm", "qwen3.7-flash", t0 + timedelta(hours=1)).tiers[0]["input"] == 0.03
        assert price_at(db, "llm", "qwen3.7-flash", t0 + timedelta(days=3)).tiers[0]["input"] == 0.05

    # 手动配置优先
    refresh(settings(llm_prices={"qwen3.7-flash": {"input": 1, "output": 2}}, currency="¥"),
            httpx.Client(transport=Pages()), now=t0 + timedelta(days=3))
    with SessionLocal() as db:
        p = price_at(db, "llm", "qwen3.7-flash", t0 + timedelta(days=4))
        assert (p.source, p.currency, p.tiers[0]["input"]) == ("manual", "CNY", 1)


def test_refresh_survives_source_failure(clean_prices):
    class Down(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

    lines = refresh(settings(), httpx.Client(transport=Down()))
    assert any("百炼定价页抓取失败" in x for x in lines) and any("OpenRouter 单价抓取失败" in x for x in lines)


def test_claim_refresh_once_per_interval(clean_prices):
    s = settings(price_refresh_hours=24)
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert pricing.claim_refresh(s, t0) is True
    assert pricing.claim_refresh(s, t0 + timedelta(hours=1)) is False
    assert pricing.claim_refresh(s, t0 + timedelta(hours=25)) is True
    assert pricing.claim_refresh(settings(price_refresh_hours=0), t0 + timedelta(days=9)) is False


def test_record_writes_cost_and_summary_uses_it(clean_prices):
    refresh(settings(), httpx.Client(transport=Pages()), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with SessionLocal() as db:
        db.execute(delete(AiUsage).where(AiUsage.model.in_(["qwen3.7-flash", "no-price-model"])))
        db.commit()
    usage.record("llm", "segment", "qwen3.7-flash", prompt_tokens=10_000, completion_tokens=1_000)
    usage.record("llm", "segment", "no-price-model", prompt_tokens=10)
    with SessionLocal() as db:
        rows = list(db.scalars(select(AiUsage).where(AiUsage.model.in_(["qwen3.7-flash", "no-price-model"]))
                               .order_by(AiUsage.id)))
    priced, unpriced = rows
    assert priced.cost == pytest.approx((10_000 * 0.03 + 1_000 * 0.13) / 1e6) and priced.currency == "USD"
    assert priced.price_id is not None and unpriced.cost is None
    sm = usage.summarize(rows)
    assert sm.cost == pytest.approx(priced.cost) and sm.currency == "$" and not sm.priced
    assert sm.unpriced_models == ["no-price-model"] and sm.costs_by_currency == {"USD": pytest.approx(priced.cost)}

    # 历史调用补算：清空成本后按当时的单价重算
    with SessionLocal() as db:
        db.get(AiUsage, priced.id).cost = None
        db.commit()
    assert pricing.backfill()[0] >= 1
    with SessionLocal() as db:
        assert db.get(AiUsage, priced.id).cost == pytest.approx(priced.cost)


def test_prices_api(client, clean_prices):  # noqa: F811
    refresh(settings(), httpx.Client(transport=Pages()), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    refresh(settings(), httpx.Client(transport=Pages(PAGE.replace("$0.030", "$0.050"))),
            now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    current = client.get("/api/usage/prices").json()
    flash = next(p for p in current if p["model"] == "qwen3.7-flash")
    assert flash["tiers"][0]["input"] == 0.05 and flash["source"] == "aliyun" and flash["currency"] == "USD"
    history = client.get("/api/usage/prices", params={"history": True}).json()
    assert [p["tiers"][0]["input"] for p in history if p["model"] == "qwen3.7-flash"] == [0.03, 0.05]
