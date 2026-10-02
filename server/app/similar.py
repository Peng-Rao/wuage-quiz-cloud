"""相似题识别。

- 字面相似度（默认，无外部依赖）：归一化后的二元 / 三元字组合做余弦相似度。
  擅长识别同一道题、只改了数字或选项顺序的同模板题。
- 语义相似度（可选）：配置 EMBEDDING_MODEL 后调用 OpenAI 兼容 /embeddings 接口，换了说法的同类题也能召回。
- 综合分 = 字面分与校准后的语义分加权；中文题目之间的向量余弦普遍偏高，需要校准到 0–1。
  阈值为经验值，接入真实题库后应按标注数据重新校准。

P1 为全量比对，适合万题以内的校本题库；题量更大时换成 pgvector 等向量索引。
"""

import logging
import heapq
import math
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from . import usage as usage_log
from .config import Settings, get_settings
from .db import BankQuestion, DraftQuestion, ParseJob

log = logging.getLogger(__name__)

# 综合分达到该值视为「题库已有相似题」（疑似重复）
DUPLICATE_SCORE = 0.85
# 低于该值的结果不展示
MIN_SCORE = 0.3
# 语义余弦校准区间：低于下限视为不相关，高于上限视为同一道题
SEMANTIC_RANGE = (0.6, 0.95)
SEMANTIC_WEIGHT = 0.5
SCAN_BATCH = 200
FEATURE_CACHE_SIZE = 256
FEATURE_CACHE_MAX_CHARS = 2000

_LATEX_CMD = re.compile(r"\\[a-zA-Z]+")
_BLANK = re.compile(r"_{2,}|＿+|—{2,}|（\s*）|\(\s*\)")
_DROP = re.compile(r"[\s$\\{}]")

# 测试时可替换为 httpx.MockTransport
transport: httpx.AsyncBaseTransport | None = None


def normalize(text: str) -> str:
    """去掉空白、标点、填空横线与 LaTeX 命令，全角转半角，小写。"""
    text = unicodedata.normalize("NFKC", text or "")
    text = _BLANK.sub("", text)
    text = _LATEX_CMD.sub("", text)
    text = _DROP.sub("", text)
    return "".join(ch for ch in text.lower() if not unicodedata.category(ch).startswith("P"))


def question_text(stem: str, options: list[str] | None = None) -> str:
    return stem + "".join(options or [])


def _features(norm: str) -> tuple[Counter, float]:
    grams = Counter(norm[i:i + 2] for i in range(len(norm) - 1))
    grams.update(norm[i:i + 3] for i in range(len(norm) - 2))
    if not grams and norm:
        grams[norm] = 1
    return grams, math.sqrt(sum(v * v for v in grams.values()))


_cached_features = lru_cache(maxsize=FEATURE_CACHE_SIZE)(_features)


def _text_features(text: str) -> tuple[Counter, float]:
    norm = normalize(text)
    # 长题只用于本次计算，避免单个缓存条目无限变大。
    return _cached_features(norm) if len(norm) <= FEATURE_CACHE_MAX_CHARS else _features(norm)


def lexical(a: str, b: str) -> float:
    """两段题目文本的字面相似度 0–1。"""
    return _feature_similarity(_text_features(a), _text_features(b))


def _feature_similarity(a: tuple[Counter, float], b: tuple[Counter, float]) -> float:
    (fa, na), (fb, nb) = a, b
    if not na or not nb:
        return 0.0
    if len(fa) > len(fb):
        fa, fb = fb, fa
    dot = sum(v * fb.get(k, 0) for k, v in fa.items())
    return dot / (na * nb)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def calibrate(cos: float) -> float:
    lo, hi = SEMANTIC_RANGE
    return max(0.0, min(1.0, (cos - lo) / (hi - lo)))


def combine(lex: float, sem_cos: float | None) -> float:
    if sem_cos is None:
        return lex
    return (1 - SEMANTIC_WEIGHT) * lex + SEMANTIC_WEIGHT * calibrate(sem_cos)


# ---------------- 向量 ----------------

def embedding_enabled(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(s.embedding_model and (s.embedding_base_url or s.llm_base_url) and (s.embedding_api_key or s.llm_api_key))


async def embed(texts: list[str], settings: Settings | None = None, *, purpose: str = "embed") -> list[list[float]] | None:
    """批量获取文本向量；未配置或调用失败时返回 None（调用方退回字面相似度）。"""
    s = settings or get_settings()
    if not texts or not embedding_enabled(s):
        return None
    url = (s.embedding_base_url or s.llm_base_url).rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {s.embedding_api_key or s.llm_api_key}"}
    out: list[list[float]] = []
    try:
        async with httpx.AsyncClient(timeout=60, transport=transport) as client:
            for i in range(0, len(texts), s.embedding_batch):
                chunk = [t[:2000] or " " for t in texts[i:i + s.embedding_batch]]
                started = time.monotonic()
                r = await client.post(url, json={"model": s.embedding_model, "input": chunk}, headers=headers)
                r.raise_for_status()
                body: dict[str, Any] = r.json()
                data = sorted(body["data"], key=lambda d: d.get("index", 0))
                out += [d["embedding"] for d in data]
                used = body.get("usage") or {}
                tokens = used.get("prompt_tokens") or used.get("total_tokens")
                usage_log.record(
                    "llm", purpose, s.embedding_model,
                    prompt_tokens=int(tokens or sum(usage_log.estimate_tokens(t) for t in chunk)),
                    duration_ms=int((time.monotonic() - started) * 1000), estimated=tokens is None,
                )
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("获取向量失败，退回字面相似度：%s", e)
        return None
    return out if len(out) == len(texts) else None


# ---------------- 检索 ----------------

@dataclass
class Match:
    kind: str  # bank / draft
    id: str
    score: float
    lexical: float
    semantic: float | None
    row: Any


def _candidates(s: Session, school_id: str, *, qtype: str | None, exclude_job: str | None, scope: str,
                with_vectors: bool):
    bank = select(BankQuestion).where(BankQuestion.school_id == school_id)
    if qtype:
        bank = bank.where(BankQuestion.type == qtype)
    def rows(stmt, cls):
        columns = [cls.id, cls.stem, cls.options]
        if with_vectors:
            columns += [cls.embedding, cls.embedding_model]
        return s.execute(stmt.with_only_columns(*columns).execution_options(yield_per=SCAN_BATCH))

    for b in rows(bank, BankQuestion):
        yield "bank", b
    if scope == "all":
        # 其他试卷中尚未入库的草稿题：用于发现同一份试卷被重复上传
        saved = bank.where(BankQuestion.source_draft_id == DraftQuestion.id).exists()
        drafts = (select(DraftQuestion).join(ParseJob, ParseJob.id == DraftQuestion.job_id)
                  .where(ParseJob.school_id == school_id, ParseJob.status == "done", ParseJob.kind.is_(None), ~saved))
        if exclude_job:
            drafts = drafts.where(DraftQuestion.job_id != exclude_job)
        if qtype:
            drafts = drafts.where(DraftQuestion.type == qtype)
        for d in rows(drafts, DraftQuestion):
            yield "draft", d


def search(
    s: Session, school_id: str, text: str, *, vector: list[float] | None = None, qtype: str | None = None,
    exclude_job: str | None = None, exclude_ids: set[str] | None = None, scope: str = "bank", limit: int = 5,
    min_score: float = MIN_SCORE, model: str | None = None,
) -> list[Match]:
    model = model or get_settings().embedding_model
    if limit <= 0:
        return []
    query_features = _text_features(text)
    # 只保留前 limit 个结果；同分时维持原候选顺序。
    best: list[tuple[float, int, Match]] = []
    for index, (kind, row) in enumerate(_candidates(
        s, school_id, qtype=qtype, exclude_job=exclude_job, scope=scope, with_vectors=bool(vector),
    )):
        if exclude_ids and row.id in exclude_ids:
            continue
        lex = _feature_similarity(query_features, _text_features(question_text(row.stem, row.options)))
        sem = None
        if vector and row.embedding and row.embedding_model == model:
            sem = cosine(vector, row.embedding)
        score = combine(lex, sem)
        if score >= min_score:
            match = Match(kind, row.id, round(score, 4), round(lex, 4), None if sem is None else round(sem, 4), row)
            item = (match.score, -index, match)
            if len(best) < limit:
                heapq.heappush(best, item)
            elif item[:2] > best[0][:2]:
                heapq.heapreplace(best, item)
    matches = [item[2] for item in sorted(best, key=lambda item: item[:2], reverse=True)]
    # 只有最终命中的题才加载展示字段，避免全库答案、解析等进入内存。
    for kind, cls in (("bank", BankQuestion), ("draft", DraftQuestion)):
        ids = [m.id for m in matches if m.kind == kind]
        if ids:
            found = {r.id: r for r in s.scalars(select(cls).where(cls.id.in_(ids)).options(defer(cls.embedding)))}
            for m in matches:
                if m.kind == kind:
                    m.row = found.get(m.id)
    return [m for m in matches if m.row is not None]


async def dedupe_job(s: Session, job_id: str, settings: Settings | None = None) -> int:
    """为本卷每道题计算向量（若启用）并与校本题库比对，标记疑似重复。返回疑似重复题数。"""
    settings = settings or get_settings()
    job = s.get(ParseJob, job_id)
    assert job is not None
    qs = list(s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id).order_by(DraftQuestion.no)))
    vectors = await embed([question_text(q.stem, q.options) for q in qs], settings)
    if vectors:
        for q, v in zip(qs, vectors):
            q.embedding, q.embedding_model = v, settings.embedding_model
    dup = 0
    for q in qs:
        best = search(s, job.school_id, question_text(q.stem, q.options), vector=q.embedding, qtype=q.type,
                      scope="bank", limit=1, min_score=DUPLICATE_SCORE)
        q.duplicate_of = best[0].id if best else None
        dup += bool(best)
    s.commit()
    return dup
