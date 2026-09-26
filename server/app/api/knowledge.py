from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import KnowledgeNode, KnowledgeTree, ParseJob, get_session
from ..knowledge_tree import TreeImportError, delete_tree, import_tree, parse_csv, parse_json, pick_tree, search_nodes, tree_json
from ..schemas import KnowledgeNodeHit, KnowledgeTreeDetail, KnowledgeTreeImport, KnowledgeTreeOut
from ..services import current_school, not_found

router = APIRouter()


def _tree(s: Session, tree_id: str) -> KnowledgeTree:
    t = s.get(KnowledgeTree, tree_id)
    if t is None or t.school_id != current_school():
        raise not_found("知识树")
    return t


@router.get("/api/knowledge-trees", response_model=list[KnowledgeTreeOut])
def list_trees(s: Session = Depends(get_session)) -> list[KnowledgeTreeOut]:
    trees = s.scalars(select(KnowledgeTree).where(KnowledgeTree.school_id == current_school())
                      .order_by(KnowledgeTree.stage, KnowledgeTree.subject, KnowledgeTree.builtin, KnowledgeTree.created_at.desc()))
    return [KnowledgeTreeOut.model_validate(t) for t in trees]


@router.post("/api/knowledge-trees/import", response_model=KnowledgeTreeOut)
def import_knowledge_tree(req: KnowledgeTreeImport, s: Session = Depends(get_session)) -> KnowledgeTreeOut:
    """导入知识树（JSON 嵌套 / CSV 路径）。同学科已有正式知识树时并存，解析时优先使用最新导入的。"""
    try:
        if req.format == "json":
            meta, nodes = parse_json(req.content)
        else:
            meta, nodes = {}, parse_csv(req.content)
        for k in ("name", "subject", "stage", "textbook"):
            if getattr(req, k):
                meta[k] = getattr(req, k).strip()
        tree = import_tree(s, current_school(), meta, nodes)
    except TreeImportError as e:
        raise HTTPException(400, str(e)) from e
    s.commit()
    return KnowledgeTreeOut.model_validate(tree)


@router.get("/api/knowledge-trees/{tree_id}", response_model=KnowledgeTreeDetail)
def read_tree(tree_id: str, s: Session = Depends(get_session)) -> KnowledgeTreeDetail:
    t = _tree(s, tree_id)
    return KnowledgeTreeDetail(**KnowledgeTreeOut.model_validate(t).model_dump(), nodes=tree_json(s, t))


@router.delete("/api/knowledge-trees/{tree_id}", status_code=204)
def remove_tree(tree_id: str, s: Session = Depends(get_session)) -> None:
    delete_tree(s, _tree(s, tree_id))
    s.commit()


@router.get("/api/knowledge/search", response_model=list[KnowledgeNodeHit])
def search_knowledge(
    q: str = Query(min_length=1, max_length=50), job_id: str | None = Query(None, alias="jobId"),
    tree_id: str | None = Query(None, alias="treeId"), limit: int = Query(10, ge=1, le=30),
    s: Session = Depends(get_session),
) -> list[KnowledgeNodeHit]:
    """知识点联想：按试卷（自动选知识树）或指定知识树检索。"""
    tree = None
    if tree_id:
        tree = _tree(s, tree_id)
    elif job_id:
        job = s.get(ParseJob, job_id)
        tree = pick_tree(s, current_school(), job.meta if job else None)
    if tree is None:
        return []
    nodes = list(s.scalars(select(KnowledgeNode).where(KnowledgeNode.tree_id == tree.id)))
    return [KnowledgeNodeHit(id=m.node.id, name=m.node.name, path=m.node.path, score=m.score)
            for m in search_nodes(nodes, q, limit=limit, min_score=0.3)]
