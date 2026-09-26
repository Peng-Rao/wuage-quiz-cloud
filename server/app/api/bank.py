from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..bank import (
    SORTS, BankFilter, PaperFilter, bank_out, knowledge_counts, list_papers, paper_detail, remove_paper, search_questions,
)
from ..db import KnowledgeTree, get_session
from ..schemas import BankQuestionPage, PaperDetail, PaperPage
from ..services import current_school, not_found

router = APIRouter()


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


@router.get("/api/bank/questions", response_model=BankQuestionPage)
def search_bank(
    stage: str | None = None, subject: str | None = None,
    node_id: str | None = Query(None, alias="nodeId", description="知识点节点，含所有下级知识点"),
    type: str | None = None,
    diff: str | None = Query(None, pattern="^(容易|适中|较难)$"),
    paper_type: str | None = Query(None, alias="paperType", description="试卷类型关键词，逗号分隔，含任一即可"),
    year: str | None = Query(None, pattern=r"^<?\d{4}$", description="2026 或 <2024（更早）"),
    q: str | None = Query(None, max_length=50),
    paper_id: str | None = Query(None, alias="paperId"),
    sort: str = Query("default", pattern="^(" + "|".join(SORTS) + ")$"),
    limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
) -> BankQuestionPage:
    """校本题库选题：按学段学科、知识点（含下级）、题型、难度、试卷类型、年份、关键词筛选。"""
    f = BankFilter(stage=stage, subject=subject, node_id=node_id, type=type, diff=diff, paper_types=_split(paper_type),
                   year=year, q=(q or "").strip() or None, paper_id=paper_id)
    rows, total = search_questions(s, current_school(), f, sort=sort, limit=limit, offset=offset)
    return BankQuestionPage(items=[bank_out(b) for b in rows], total=total)


@router.get("/api/bank/knowledge-counts", response_model=dict[str, int])
def bank_knowledge_counts(tree_id: str = Query(alias="treeId"), s: Session = Depends(get_session)) -> dict[str, int]:
    """知识树各节点（含下级）关联的入库题数，没有题的节点不返回。"""
    tree = s.get(KnowledgeTree, tree_id)
    if tree is None or tree.school_id != current_school():
        raise not_found("知识树")
    return knowledge_counts(s, current_school(), tree)


@router.get("/api/papers", response_model=PaperPage)
def papers(
    stage: str | None = None, grade: str | None = None, subject: str | None = None,
    paper_type: str | None = Query(None, alias="paperType"), q: str | None = Query(None, max_length=50),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
) -> PaperPage:
    """试卷库：已入库的整卷，最近入库的在前；facets 为各维度的试卷数，用于按年级、学科浏览。"""
    f = PaperFilter(stage=stage, grade=grade, subject=subject, paper_type=paper_type, q=(q or "").strip() or None)
    return list_papers(s, current_school(), f, limit=limit, offset=offset)


@router.delete("/api/papers/{paper_id}", status_code=204)
def delete_paper(paper_id: str, s: Session = Depends(get_session)) -> None:
    """移出试卷库：删除该卷已入库的题，草稿题恢复为未保存，可在核对页重新保存。"""
    if not remove_paper(s, current_school(), paper_id):
        raise not_found("试卷")
    s.commit()


@router.get("/api/papers/{paper_id}", response_model=PaperDetail)
def paper(paper_id: str, s: Session = Depends(get_session)) -> PaperDetail:
    got = paper_detail(s, current_school(), paper_id)
    if got is None:
        raise not_found("试卷")
    return got
