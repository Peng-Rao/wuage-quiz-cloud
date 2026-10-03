from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..bank import (
    SORTS, BankFilter, PaperFilter, bank_out, chapter_counts, knowledge_counts, list_papers, paper_detail,
    question_facets, remove_paper, search_questions,
)
from ..auth import require_subject, staff
from ..chapters import versions
from ..db import BankQuestion, DraftQuestion, KnowledgeTree, get_session
from ..pipeline.answer import pick_bank_questions
from ..schemas import (
    AnswerTask, BankQuestionPage, GenerateBankAnswersRequest, Model, PaperDetail, PaperPage, QuestionFacets,
    TextbookVersion,
)
from ..services import current_school, get_job, not_found
from .jobs import check_answer_task, queue_answer_task

router = APIRouter()


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


@router.get("/api/bank/questions", response_model=BankQuestionPage)
def search_bank(
    stage: str | None = None, subject: str | None = None,
    node_id: str | None = Query(None, alias="nodeId", description="知识点节点，含所有下级知识点；逗号分隔多个时含任一即可"),
    chapter_id: str | None = Query(None, alias="chapterId", description="教材章或节，见 /api/chapters"),
    type: str | None = None,
    diff: str | None = Query(None, pattern="^(容易|适中|较难)$"),
    paper_type: str | None = Query(None, alias="paperType", description="试卷类型或名称关键词，逗号分隔，含任一即可"),
    year: str | None = Query(None, pattern=r"^<?\d{4}$", description="2026 或 <2024（更早）"),
    region: str | None = Query(None, description="省级地区，如 北京"),
    grade: str | None = None,
    term: str | None = Query(None, pattern="^(上|下)$", description="学期"),
    q: str | None = Query(None, max_length=50),
    paper_id: str | None = Query(None, alias="paperId"),
    sort: str = Query("default", pattern="^(" + "|".join(SORTS) + ")$"),
    limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
) -> BankQuestionPage:
    """校本题库选题：按学段学科、知识点（含下级）或教材章节、题型、难度、场景、年份、地区、年级、学期、关键词筛选。"""
    f = BankFilter(stage=stage, subject=subject, node_ids=_split(node_id), chapter_id=chapter_id, type=type, diff=diff,
                   paper_types=_split(paper_type), year=year, region=region, grade=grade, term=term,
                   q=(q or "").strip() or None, paper_id=paper_id)
    rows, total = search_questions(s, current_school(), f, sort=sort, limit=limit, offset=offset)
    return BankQuestionPage(items=[bank_out(b) for b in rows], total=total)


@router.get("/api/bank/knowledge-counts", response_model=dict[str, int])
def bank_knowledge_counts(tree_id: str = Query(alias="treeId"), s: Session = Depends(get_session)) -> dict[str, int]:
    """知识树各节点（含下级）关联的入库题数，没有题的节点不返回。"""
    tree = s.get(KnowledgeTree, tree_id)
    if tree is None or tree.school_id != current_school():
        raise not_found("知识树")
    return knowledge_counts(s, current_school(), tree)


@router.get("/api/bank/facets", response_model=QuestionFacets)
def bank_facets(stage: str | None = None, subject: str | None = None, s: Session = Depends(get_session)) -> QuestionFacets:
    """选题「更多」筛选的可选值（地区、年级、年份）及题数。"""
    return QuestionFacets(**question_facets(s, current_school(), stage, subject))


@router.get("/api/chapters", response_model=list[TextbookVersion])
def chapters(stage: str, subject: str, s: Session = Depends(get_session)) -> list[TextbookVersion]:
    """学段学科的教材版本、册与章节目录。"""
    require_subject(s.info["user"], subject)
    result = [TextbookVersion.model_validate(v) for v in versions(stage, subject)]
    if s.info["user"].role == "member":
        for version in result:
            for book in version.books:
                counts = chapter_counts(s, current_school(), book.id) or {}
                book.chapters = [chapter for chapter in book.chapters if counts.get(chapter.id)]
                for chapter in book.chapters:
                    chapter.sections = [section for section in chapter.sections if counts.get(section.id)]
                    for section in chapter.sections:
                        # The public catalog includes unrelated sibling knowledge labels.
                        section.knowledge = []
            version.books = [book for book in version.books if book.chapters]
        result = [version for version in result if version.books]
    return result


@router.get("/api/bank/chapter-counts", response_model=dict[str, int])
def bank_chapter_counts(book_id: str = Query(alias="bookId"), s: Session = Depends(get_session)) -> dict[str, int]:
    """某册教材各章、节的入库题数，没有题的不返回。"""
    got = chapter_counts(s, current_school(), book_id)
    if got is None:
        raise not_found("教材")
    return got


@router.get("/api/papers", response_model=PaperPage)
def papers(
    stage: str | None = None, grade: str | None = None, subject: str | None = None,
    paper_type: str | None = Query(None, alias="paperType"), textbook: str | None = None,
    category: str | None = Query(None, description="试卷分类关键词，逗号分隔，试卷类型或名称含任一即可"),
    q: str | None = Query(None, max_length=50),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
) -> PaperPage:
    """试卷库：已入库的整卷，最近入库的在前；facets 为各维度的试卷数，用于按年级、学科浏览。"""
    f = PaperFilter(stage=stage, grade=grade, subject=subject, paper_type=paper_type, textbook=textbook,
                    category=_split(category), q=(q or "").strip() or None)
    return list_papers(s, current_school(), f, limit=limit, offset=offset)


@router.delete("/api/papers/{paper_id}", status_code=204, dependencies=[Depends(staff)])
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


@router.post("/api/papers/{paper_id}/generate-answers", response_model=AnswerTask, status_code=202,
             dependencies=[Depends(staff)])
def generate_paper_answers(paper_id: str, req: GenerateBankAnswersRequest, s: Session = Depends(get_session)) -> AnswerTask:
    """为本卷已入库、缺少答案的题（或其中指定的题）生成 AI 答案，直接写入题库；进度见 answer-task。
    生成答案的题撤销审核，老师核对后重新审核。"""
    job = get_job(s, paper_id)  # 组长只能查到授权学科的试卷
    check_answer_task(job)
    return queue_answer_task(s, job, pick_bank_questions(s, paper_id, req.question_ids), scope="bank")


@router.get("/api/papers/{paper_id}/answer-task", response_model=AnswerTask | None, dependencies=[Depends(staff)])
def paper_answer_task(paper_id: str, s: Session = Depends(get_session)) -> AnswerTask | None:
    """本卷最近一次 AI 生成答案任务的进度（含在试卷解析页发起的）。"""
    job = get_job(s, paper_id)
    return AnswerTask.model_validate(job.answer_task) if job.answer_task else None


class BasketCheck(Model):
    ids: list[str] = Field(max_length=1000)


@router.post("/api/basket/validate", status_code=204)
def validate_basket(body: BasketCheck, s: Session = Depends(get_session)) -> None:
    """Recheck current access before exporting a previously loaded question snapshot."""
    wanted = set(body.ids)
    allowed = set(s.scalars(select(BankQuestion.id).where(BankQuestion.id.in_(wanted))))
    if s.info["user"].role != "member":
        allowed.update(s.scalars(select(DraftQuestion.id).where(DraftQuestion.id.in_(wanted))))
    if wanted - allowed:
        raise HTTPException(403, "部分题目的授权已撤回或题目已更新，请清空试题篮后重新选题")
