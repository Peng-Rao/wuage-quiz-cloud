"""相似题识别。

- 字面相似度（默认，无外部依赖）：归一化后的二元 / 三元字组合做余弦相似度。
  擅长识别同一道题、只改了数字或选项顺序的同模板题。
- 语义相似度（可选）：远程 /embeddings 或 LangChain Hugging Face 本地模型，换了说法的同类题也能召回。
- 综合分 = 字面分与校准后的语义分加权；中文题目之间的向量余弦普遍偏高，需要校准到 0–1。
  阈值为经验值，接入真实题库后应按标注数据重新校准。

大范围查询由 HNSW 和文字近邻索引分别召回候选，再按原混合分精排；小范围保持精确比对。
"""

import logging
import asyncio
import heapq
import math
import re
import time
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
from sqlalchemy import Float, case, cast, func, literal, select, text as sql_text
from sqlalchemy.dialects.postgresql import BIT
from pgvector.sqlalchemy import Vector
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
EXACT_SEARCH_LIMIT = 1000
SEARCH_TEXT_MAX_CHARS = 2000
# pgvector 的 HNSW 只支持 2000 维以内的 vector；更高维（如 4096）改用二值量化索引召回，再按原向量精排
HNSW_MAX_DIMS = 2000
# multimodal 接口逐条请求，限流或服务端错误时重试
EMBED_RETRIES = 4
EMBED_RETRY_DELAY = 1.0

_LATEX_CMD = re.compile(r"\\[a-zA-Z]+")
_BLANK = re.compile(r"_{2,}|＿+|—{2,}|（\s*）|\(\s*\)")
_DROP = re.compile(r"[\s$\\{}]")

# 测试时可替换为 httpx.MockTransport
transport: httpx.AsyncBaseTransport | None = None
_model_lock = threading.Lock()


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
    if s.embedding_provider == "huggingface":
        return bool(s.embedding_model)
    return bool(s.embedding_model and (s.embedding_base_url or s.llm_base_url) and (s.embedding_api_key or s.llm_api_key))


@lru_cache(maxsize=1)
def _local_embeddings(model: str, device: str, cache: str, batch: int):
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(
        model_name=model, cache_folder=cache, model_kwargs={"device": device, "trust_remote_code": False},
        encode_kwargs={"normalize_embeddings": True, "batch_size": batch},
    )


def _embed_local(texts: list[str], s: Settings) -> list[list[float]]:
    with _model_lock:
        model = _local_embeddings(s.embedding_model, s.embedding_device, s.embedding_cache_dir, s.embedding_batch)
    return model.embed_documents([t[:2000] or " " for t in texts])


async def embed(texts: list[str], settings: Settings | None = None, *, purpose: str = "embed") -> list[list[float] | None] | None:
    """批量获取文本向量；未配置或调用失败时返回 None（调用方退回字面相似度）。"""
    s = settings or get_settings()
    if not texts or not embedding_enabled(s):
        return None
    if s.embedding_provider == "huggingface":
        try:
            return await asyncio.to_thread(_embed_local, texts, s)
        except (ImportError, OSError, RuntimeError, ValueError) as e:
            log.warning("本地嵌入模型失败，退回字面相似度：%s", e)
            return None
    if s.embedding_provider == "multimodal":
        return await _embed_multimodal(texts, s, purpose)
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


async def _post_with_retry(client: httpx.AsyncClient, url: str, payload: dict, headers: dict) -> dict[str, Any]:
    for attempt in range(EMBED_RETRIES):
        try:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return r.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            retryable = isinstance(e, httpx.TransportError) or e.response.status_code in (408, 429) \
                or e.response.status_code >= 500
            if not retryable or attempt == EMBED_RETRIES - 1:
                raise
            await asyncio.sleep(EMBED_RETRY_DELAY * 2 ** attempt)
    raise AssertionError("unreachable")


async def _embed_multimodal(texts: list[str], s: Settings, purpose: str) -> list[list[float] | None] | None:
    """/embeddings/multimodal：每次请求返回一条融合向量，逐条并发请求。
    接口拒绝空白文本，题干为空（如纯图片题）的位置返回 None，不计为失败。"""
    url = (s.embedding_base_url or s.llm_base_url).rstrip("/") + "/embeddings/multimodal"
    headers = {"Authorization": f"Bearer {s.embedding_api_key or s.llm_api_key}"}
    limit = asyncio.Semaphore(s.embedding_concurrency)

    failed = object()

    async def one(client: httpx.AsyncClient, text: str):
        text = text[:2000]
        if not text.strip():
            return None
        async with limit:
            started = time.monotonic()
            try:
                body = await _post_with_retry(client, url, {
                    "model": s.embedding_model, "input": [{"type": "text", "text": text}], "encoding_format": "float",
                }, headers)
                vector = [float(x) for x in body["data"][0]["embedding"]]
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
                log.warning("获取向量失败，退回字面相似度：%s", e)
                return failed
            used = body.get("usage") or {}
            tokens = used.get("prompt_tokens") or used.get("total_tokens")
            usage_log.record(
                "llm", purpose, s.embedding_model,
                prompt_tokens=int(tokens or usage_log.estimate_tokens(text)),
                duration_ms=int((time.monotonic() - started) * 1000), estimated=tokens is None,
            )
            return vector

    async with httpx.AsyncClient(timeout=60, transport=transport) as client:
        out = await asyncio.gather(*(one(client, t) for t in texts))
    return None if any(v is failed for v in out) else list(out)


def ann_order(column, vector: list[float]):
    """语义近邻排序表达式，须与 search_indexes.vector_index 的索引表达式一致才能走索引。"""
    dims = len(vector)
    if dims <= HNSW_MAX_DIMS:
        return cast(column, Vector(dims)).cosine_distance(vector)
    query = func.binary_quantize(cast(literal(vector, Vector(dims)), Vector(dims)), type_=BIT(dims))
    return binary_code(column, dims).op("<~>", return_type=Float)(query)


def binary_code(column, dims: int):
    return cast(func.binary_quantize(cast(column, Vector(dims))), BIT(dims))


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
                with_vectors: bool, vector=None, model=None, query_text="", exclude_ids=None, pool=200):
    bank = select(BankQuestion).where(BankQuestion.school_id == school_id)
    if qtype:
        bank = bank.where(BankQuestion.type == qtype)
    def rows(stmt, cls):
        if exclude_ids:
            stmt = stmt.where(cls.id.not_in(exclude_ids))
        columns = [cls.id, cls.stem, cls.options]
        distance = None
        if with_vectors:
            distance = cast(cls.embedding, Vector(len(vector))).cosine_distance(vector)
            # CASE 避免模型切换后不同维度的历史向量参与距离计算。
            columns += [case(
                ((cls.embedding_model == model) & (func.vector_dims(cls.embedding) == len(vector)),
                 1 - distance),
                else_=None,
            ).label("semantic")]
        # 只探测有限个 ID，避免为判断规模遍历题目正文。
        probe = s.execute(stmt.with_only_columns(cls.id).limit(EXACT_SEARCH_LIMIT + 1)).all()
        small = len(probe) <= EXACT_SEARCH_LIMIT
        incomplete = False if small else s.scalar(stmt.with_only_columns(cls.id)
                                                  .where(cls.search_text.is_(None)).limit(1)) is not None
        if small or incomplete:
            yield from s.execute(stmt.with_only_columns(*columns).order_by(cls.id)
                                 .execution_options(yield_per=SCAN_BATCH))
            return
        # 文字召回在没有向量、向量过期或模型切换时也生效。
        lexical_distance = cls.search_text.op("<->", return_type=Float)(normalize(query_text)[:SEARCH_TEXT_MAX_CHARS])
        recalled = {}
        for row in s.execute(stmt.with_only_columns(*columns).order_by(lexical_distance).limit(pool)):
            recalled[row.id] = row
        if with_vectors:
            semantic = stmt.where(
                cls.embedding_model == literal(model, literal_execute=True),
                func.vector_dims(cls.embedding) == literal(len(vector), literal_execute=True),
            ).with_only_columns(*columns).order_by(ann_order(cls.embedding, vector)).limit(pool)
            for row in s.execute(semantic):
                recalled[row.id] = row
        yield from (recalled[key] for key in sorted(recalled))

    for b in rows(bank, BankQuestion):
        yield "bank", b
    if scope == "all":
        # 其他试卷中尚未入库的草稿题：用于发现同一份试卷被重复上传
        saved = select(BankQuestion.id).where(BankQuestion.school_id == school_id,
                                              BankQuestion.source_draft_id == DraftQuestion.id).exists()
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
    if vector and (not all(math.isfinite(v) for v in vector) or not any(vector)):
        vector = None
    if vector:
        # 过滤条件在 ANN 扫描后应用；迭代扫描继续找足匹配学校/题型的候选。
        s.execute(sql_text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
        s.execute(sql_text("SET LOCAL hnsw.ef_search = 200"))
    pool = max(get_settings().similar_candidates, limit * 10)
    query_features = _text_features(text)
    # 只保留前 limit 个结果；同分时维持原候选顺序。
    best: list[tuple[float, int, Match]] = []
    for index, (kind, row) in enumerate(_candidates(
        s, school_id, qtype=qtype, exclude_job=exclude_job, scope=scope, with_vectors=bool(vector),
        vector=vector, model=model, query_text=text, exclude_ids=exclude_ids, pool=pool,
    )):
        if exclude_ids and row.id in exclude_ids:
            continue
        lex = _feature_similarity(query_features, _text_features(question_text(row.stem, row.options)))
        sem = row.semantic if vector else None
        if sem is not None and not math.isfinite(sem):
            sem = None
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
            q.embedding, q.embedding_model = v, settings.embedding_model if v else None
    dup = 0
    for q in qs:
        best = search(s, job.school_id, question_text(q.stem, q.options), vector=q.embedding, qtype=q.type,
                      scope="bank", limit=1, min_score=DUPLICATE_SCORE, model=settings.embedding_model)
        q.duplicate_of = best[0].id if best else None
        dup += bool(best)
    s.commit()
    return dup
