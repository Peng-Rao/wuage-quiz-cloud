"""校本题库检索（章节 / 知识点选题）与试卷库。

- 知识点筛选按节点 id 或路径匹配：内置知识树更新后节点 id 会重建，路径不变，已入库题的知识点仍能归入节点。
- 学段、学科、题型、难度在数据库中筛选；知识点、试卷类型、年份、关键词在内存中筛选。
  单校题量达到数万后，应改为「题目—知识点」关联表并全部在数据库中筛选。
- 试卷库中的一份试卷 = 同一份原卷（解析任务）入库的题，分类信息取自解析任务。
"""

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import BankQuestion, DraftQuestion, KnowledgeNode, KnowledgeTree, ParseJob
from .knowledge_tree import PATH_SEP
from .schemas import (
    BankQuestionOut, DuplicatePaper, FacetCount, PaperDetail, PaperFacets, PaperMeta, PaperPage, PaperSummary,
)
from .services import question_source
from .similar import DUPLICATE_SCORE, normalize, question_text, search
from .storage import get_store

# 与前端 coefToDiff 一致：≤ 0.3 容易，≤ 0.6 适中，其余较难
DIFF_RANGES: dict[str, tuple[float | None, float | None]] = {"容易": (None, 0.3), "适中": (0.3, 0.6), "较难": (0.6, None)}
SORTS = ("default", "latest", "easy", "hard")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


@dataclass
class BankFilter:
    stage: str | None = None
    subject: str | None = None
    node_id: str | None = None
    type: str | None = None
    diff: str | None = None
    # 试卷类型包含任一关键词，如 ["期中", "期末"]
    paper_types: list[str] = field(default_factory=list)
    # 「2026」：学年或试卷名称中含该年份；「<2024」：最早年份早于 2024
    year: str | None = None
    q: str | None = None
    paper_id: str | None = None


# ---------------- 知识点 ----------------

def subtree(s: Session, node: KnowledgeNode) -> tuple[set[str], str]:
    """节点及其所有下级的 id，以及节点路径。"""
    prefix = node.path + PATH_SEP
    ids = set(s.scalars(select(KnowledgeNode.id).where(
        KnowledgeNode.tree_id == node.tree_id, KnowledgeNode.path.startswith(prefix, autoescape=True))))
    ids.add(node.id)
    return ids, node.path


def kp_in_subtree(kps: list[dict[str, Any]], ids: set[str], path: str) -> bool:
    for k in kps or []:
        if k.get("id") in ids:
            return True
        p = k.get("path")
        if p and (p == path or p.startswith(path + PATH_SEP)):
            return True
    return False


def knowledge_counts(s: Session, school_id: str, tree: KnowledgeTree) -> dict[str, int]:
    """知识树各节点（含下级）关联的入库题数，只统计与知识树同学段、学科的题。"""
    nodes = list(s.scalars(select(KnowledgeNode).where(KnowledgeNode.tree_id == tree.id)))
    by_id = {n.id: n for n in nodes}
    by_path = {n.path: n for n in nodes}
    rows = s.execute(select(BankQuestion.knowledge_points).where(
        BankQuestion.school_id == school_id,
        BankQuestion.meta["stage"].as_string() == tree.stage,
        BankQuestion.meta["subject"].as_string() == tree.subject,
    ))
    counts: Counter[str] = Counter()
    for (kps,) in rows:
        hit: set[str] = set()
        for k in kps or []:
            n = by_id.get(k.get("id")) or by_path.get(k.get("path") or "")
            # 计入节点及其所有上级；上级已计入时其祖先必然也已计入
            while n is not None and n.id not in hit:
                hit.add(n.id)
                n = by_id.get(n.parent_id or "")
        counts.update(hit)
    return dict(counts)


# ---------------- 题目 ----------------

def _years(*texts: str) -> list[int]:
    return [int(y) for t in texts if t for y in _YEAR.findall(t)]


def _year_ok(meta: dict[str, Any], cond: str) -> bool:
    years = _years(meta.get("schoolYear") or meta.get("school_year") or "", meta.get("title") or "")
    if not years:
        return False
    if cond.startswith("<"):
        return min(years) < int(cond[1:])
    return int(cond) in years


def _paper_type(meta: dict[str, Any]) -> str:
    return meta.get("paperType") or meta.get("paper_type") or ""


def search_questions(s: Session, school_id: str, f: BankFilter, sort: str = "default",
                     limit: int = 10, offset: int = 0) -> tuple[list[BankQuestion], int]:
    stmt = select(BankQuestion).where(BankQuestion.school_id == school_id)
    if f.stage:
        stmt = stmt.where(BankQuestion.meta["stage"].as_string() == f.stage)
    if f.subject:
        stmt = stmt.where(BankQuestion.meta["subject"].as_string() == f.subject)
    if f.type:
        stmt = stmt.where(BankQuestion.type == f.type)
    if f.paper_id:
        stmt = stmt.where(BankQuestion.source_job_id == f.paper_id)
    if f.diff in DIFF_RANGES:
        lo, hi = DIFF_RANGES[f.diff]
        if lo is not None:
            stmt = stmt.where(BankQuestion.coef > lo)
        if hi is not None:
            stmt = stmt.where(BankQuestion.coef <= hi)

    if sort == "latest":
        stmt = stmt.order_by(BankQuestion.created_at.desc(), BankQuestion.source_no)
    elif sort in ("easy", "hard"):
        stmt = stmt.order_by(BankQuestion.coef if sort == "easy" else BankQuestion.coef.desc(), BankQuestion.created_at.desc())
    else:
        # 综合排序：按试卷（最新上传的在前），卷内按题号
        stmt = (stmt.outerjoin(ParseJob, ParseJob.id == BankQuestion.source_job_id)
                .order_by(ParseJob.created_at.desc(), BankQuestion.source_job_id, BankQuestion.source_no))

    node = s.get(KnowledgeNode, f.node_id) if f.node_id else None
    sub = subtree(s, node) if node else None
    kw = normalize(f.q or "")

    def keep(b: BankQuestion) -> bool:
        m = b.meta or {}
        if f.node_id and (sub is None or not kp_in_subtree(b.knowledge_points, *sub)):
            return False
        if f.paper_types and not any(t in _paper_type(m) for t in f.paper_types):
            return False
        if f.year and not _year_ok(m, f.year):
            return False
        if kw:
            names = " ".join(k.get("name") or "" for k in b.knowledge_points or [])
            hay = normalize(" ".join([b.stem, *b.options, names, m.get("title") or "", b.source_file_name or ""]))
            if kw not in hay:
                return False
        return True

    rows = [b for b in s.scalars(stmt) if keep(b)]
    return rows[offset:offset + limit], len(rows)


def bank_out(b: BankQuestion) -> BankQuestionOut:
    store = get_store()
    return BankQuestionOut(
        id=b.id, type=b.type, score=b.score, stem=b.stem, options=b.options or [], answer=b.answer, analysis=b.analysis,
        answer_source=b.answer_source, knowledge_points=b.knowledge_points or [], coef=b.coef,
        images=[store.public_url(k) for k in b.images or []],
        source=question_source(b.meta, b.source_file_name, b.source_no, b.source_page),
        paper_id=b.source_job_id, created_at=b.created_at,
    )


# ---------------- 试卷库 ----------------

@dataclass
class PaperFilter:
    stage: str | None = None
    grade: str | None = None
    subject: str | None = None
    paper_type: str | None = None
    q: str | None = None


def _summaries(s: Session, school_id: str, job_ids: list[str] | None = None) -> list[PaperSummary]:
    where = [BankQuestion.school_id == school_id]
    if job_ids is not None:
        where.append(BankQuestion.source_job_id.in_(job_ids))
    agg = s.execute(select(
        BankQuestion.source_job_id, func.count(), func.sum(BankQuestion.score), func.sum(BankQuestion.coef * BankQuestion.score),
        func.avg(BankQuestion.coef), func.max(BankQuestion.created_at),
    ).where(*where).group_by(BankQuestion.source_job_id)).all()
    if not agg:
        return []
    ids = [r[0] for r in agg]
    types: dict[str, dict[str, int]] = defaultdict(dict)
    for jid, t, n in s.execute(select(BankQuestion.source_job_id, BankQuestion.type, func.count())
                               .where(*where).group_by(BankQuestion.source_job_id, BankQuestion.type)):
        types[jid][t] = n
    drafts = dict(s.execute(select(DraftQuestion.job_id, func.count()).where(DraftQuestion.job_id.in_(ids))
                            .group_by(DraftQuestion.job_id)).all())
    jobs = {j.id: j for j in s.scalars(select(ParseJob).where(ParseJob.id.in_(ids)))}
    out = []
    for jid, n, score, weighted, avg, updated in agg:
        job = jobs.get(jid)
        meta = PaperMeta.model_validate((job.meta if job else None) or {})
        file_name = job.file_name if job else ""
        out.append(PaperSummary(
            id=jid, title=meta.title or file_name.rsplit(".", 1)[0] or "未命名试卷", meta=meta, file_name=file_name,
            question_count=n, source_question_count=max(drafts.get(jid, 0), n), total_score=score or 0,
            type_counts=types[jid], avg_coef=round(weighted / score if score else avg, 2) if avg is not None else None,
            updated_at=updated,
        ))
    return out


UNCLASSIFIED = "未分类"


def _paper_match(p: PaperSummary, f: PaperFilter, skip: str | None = None) -> bool:
    for dim in ("stage", "grade", "subject", "paper_type"):
        want = getattr(f, dim)
        if want and skip != dim and (getattr(p.meta, dim) or UNCLASSIFIED) != want:
            return False
    m = p.meta
    if f.q:
        kw = normalize(f.q)
        if kw not in normalize(" ".join([p.title, p.file_name, m.region, m.school_year, m.textbook])):
            return False
    return True


def _facet(papers: list[PaperSummary], f: PaperFilter, dim: str, order: list[str] | None = None) -> list[FacetCount]:
    c = Counter(getattr(p.meta, dim) or UNCLASSIFIED for p in papers if _paper_match(p, f, skip=dim))
    rank = {v: i for i, v in enumerate(order or [])}
    return [FacetCount(name=k, count=v) for k, v in sorted(c.items(), key=lambda x: (rank.get(x[0], len(rank)), -x[1], x[0]))]


# 年级按学段顺序排列，其余按试卷数
GRADE_ORDER = ["一年级", "二年级", "三年级", "四年级", "五年级", "六年级", "七年级", "初一", "八年级", "初二", "九年级", "初三",
               "高一", "高二", "高三"]
STAGE_ORDER = ["小学", "初中", "高中"]


def list_papers(s: Session, school_id: str, f: PaperFilter, limit: int = 20, offset: int = 0) -> PaperPage:
    papers = sorted(_summaries(s, school_id), key=lambda p: p.updated_at, reverse=True)
    matched = [p for p in papers if _paper_match(p, f)]
    facets = PaperFacets(
        stages=_facet(papers, f, "stage", STAGE_ORDER), grades=_facet(papers, f, "grade", GRADE_ORDER),
        subjects=_facet(papers, f, "subject"), paper_types=_facet(papers, f, "paper_type"),
    )
    return PaperPage(items=matched[offset:offset + limit], total=len(matched), facets=facets)


def paper_detail(s: Session, school_id: str, paper_id: str) -> PaperDetail | None:
    got = _summaries(s, school_id, [paper_id])
    if not got:
        return None
    qs = s.scalars(select(BankQuestion).where(BankQuestion.school_id == school_id, BankQuestion.source_job_id == paper_id)
                   .order_by(BankQuestion.source_no))
    return PaperDetail(**got[0].model_dump(), questions=[bank_out(b) for b in qs])


def sync_meta(s: Session, job: ParseJob) -> None:
    """试卷分类修改后，同步到已入库题目的快照，按学段学科、年份等检索时以最新分类为准。"""
    for b in s.scalars(select(BankQuestion).where(BankQuestion.source_job_id == job.id)):
        b.meta = job.meta


# ---------------- 重复入库检查 ----------------

def compute_file_hash(keys: list[str]) -> str:
    """各文件 SHA-256 按顺序拼接后再取 SHA-256：同一组文件（同一份试卷）得到相同指纹。"""
    store = get_store()
    outer = hashlib.sha256()
    for key in keys:
        outer.update(hashlib.sha256(store.get(key)).digest())
    return outer.hexdigest()


def ensure_file_hash(job: ParseJob) -> str | None:
    """旧任务没有指纹时补算；原文件已不存在则返回 None。"""
    if job.file_hash is None:
        keys = [f["key"] for f in job.file_keys or []]
        store = get_store()
        if keys and all(store.exists(k) for k in keys):
            job.file_hash = compute_file_hash(keys)
    return job.file_hash


# 本卷选中的题中，与同一份已入库试卷重复的比例达到该值（且至少 3 题）时，视为同一份试卷
SAME_PAPER_RATIO = 0.5


@dataclass
class DuplicateCheck:
    # 草稿题 id → (已有题目, 相似度)
    questions: dict[str, tuple[BankQuestion, float]]
    paper: DuplicatePaper | None


def check_duplicates(s: Session, job: ParseJob, qs: list[DraftQuestion]) -> DuplicateCheck:
    """入库前检查：逐题与其他试卷已入库的题比对；再判断整份试卷是否已在试卷库中
    （相同文件、同学段学科的同名试卷，或多数题目与同一份已入库试卷重复）。本卷自己已入库的题不算重复。"""
    own = set(s.scalars(select(BankQuestion.id).where(BankQuestion.source_job_id == job.id)))
    found: dict[str, tuple[BankQuestion, float]] = {}
    for q in qs:
        best = search(s, job.school_id, question_text(q.stem, q.options), vector=q.embedding, qtype=q.type,
                      exclude_ids=own, scope="bank", limit=1, min_score=DUPLICATE_SCORE)
        if best:
            found[q.id] = (best[0].row, best[0].score)

    others = [jid for jid in s.scalars(select(BankQuestion.source_job_id).where(
        BankQuestion.school_id == job.school_id, BankQuestion.source_job_id != job.id).distinct())]
    jobs = {j.id: j for j in s.scalars(select(ParseJob).where(ParseJob.id.in_(others)))} if others else {}

    def dup(j: ParseJob, reason: str) -> DuplicatePaper:
        meta = PaperMeta.model_validate(j.meta or {})
        return DuplicatePaper(id=j.id, title=meta.title or j.file_name.rsplit(".", 1)[0], reason=reason)

    paper = None
    mine = ensure_file_hash(job)
    if mine:
        same = next((j for j in jobs.values() if ensure_file_hash(j) == mine), None)
        paper = dup(same, "same_file") if same else None
    if paper is None:
        m = job.meta or {}
        title = normalize(m.get("title") or "")
        # 太短的名称（如「数学试卷」）不足以说明是同一份试卷
        if len(title) >= 8:
            for j in jobs.values():
                o = j.meta or {}
                if normalize(o.get("title") or "") == title and o.get("stage") == m.get("stage") \
                        and o.get("subject") == m.get("subject"):
                    paper = dup(j, "same_title")
                    break
    if paper is None and qs:
        by_paper = Counter(b.source_job_id for b, _ in found.values())
        if by_paper:
            jid, n = by_paper.most_common(1)[0]
            if n >= 3 and n / len(qs) >= SAME_PAPER_RATIO and jid in jobs:
                paper = dup(jobs[jid], "most_questions")
    return DuplicateCheck(questions=found, paper=paper)


def remove_paper(s: Session, school_id: str, paper_id: str) -> int:
    """把整份试卷移出试卷库：删除已入库的题，草稿题恢复为未保存（可在核对页重新保存）。返回删除的题数。"""
    rows = list(s.scalars(select(BankQuestion).where(BankQuestion.school_id == school_id,
                                                     BankQuestion.source_job_id == paper_id)))
    for b in rows:
        s.delete(b)
    for d in s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == paper_id, DraftQuestion.status == "saved")):
        d.status = "draft"
    return len(rows)
