"""知识树：导入、内置示例、按试卷选择、知识点检索。

导入格式：
- JSON：{"name", "subject", "stage", "textbook", "nodes": [{"name", "aliases": [...], "children": [...]}]}
- CSV：每行一条路径，列为各级名称（一级, 二级, 三级, …），可选最后一列「别名」（多个用 | 或 、 分隔）；
  首行若为表头（含「级」「层」「名称」「知识点」等字样）自动跳过。
"""

import csv
import hashlib
import io
import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import KnowledgeNode, KnowledgeTree
from .similar import lexical, normalize

log = logging.getLogger(__name__)

BUILTIN_DIR = Path(__file__).parent / "data" / "knowledge"
PATH_SEP = " / "
_ALIAS_SPLIT = re.compile(r"[|、；;]")
_HEADER_HINT = re.compile(r"级|层|名称|知识点|章|节|别名")


class TreeImportError(ValueError):
    pass


# ---------------- 解析 ----------------

def parse_json(content: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise TreeImportError(f"JSON 格式错误：第 {e.lineno} 行") from e
    if isinstance(data, list):
        data = {"nodes": data}
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) or not data["nodes"]:
        raise TreeImportError("JSON 中缺少 nodes（知识点列表）")
    meta = {k: str(data.get(k) or "") for k in ("name", "subject", "stage", "textbook")}
    return meta, data["nodes"]


def parse_csv(content: str) -> list[dict[str, Any]]:
    """把「每行一条路径」转换为嵌套节点。"""
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(content.lstrip("﻿"))) if any(c.strip() for c in r)]
    if rows and all(_HEADER_HINT.search(c) for c in rows[0] if c):
        header, rows = rows[0], rows[1:]
    else:
        header = []
    alias_col = next((i for i, h in enumerate(header) if "别名" in h), None)
    root: list[dict[str, Any]] = []
    for r in rows:
        aliases = [a.strip() for a in _ALIAS_SPLIT.split(r[alias_col]) if a.strip()] if alias_col is not None and alias_col < len(r) else []
        path = [c for i, c in enumerate(r) if c and i != alias_col]
        level = root
        for depth, name in enumerate(path):
            node = next((x for x in level if x["name"] == name), None)
            if node is None:
                node = {"name": name, "children": []}
                level.append(node)
            if depth == len(path) - 1 and aliases:
                node["aliases"] = sorted(set(node.get("aliases", []) + aliases))
            level = node["children"]
    if not root:
        raise TreeImportError("CSV 中没有知识点")
    return root


# ---------------- 写入 ----------------

def import_tree(s: Session, school_id: str, meta: dict[str, str], nodes: list[dict[str, Any]], *, builtin: bool = False,
                builtin_key: str | None = None, builtin_version: str | None = None) -> KnowledgeTree:
    if not meta.get("subject") or not meta.get("stage"):
        raise TreeImportError("请指定学科与学段")
    tree = KnowledgeTree(id="t" + uuid.uuid4().hex[:12], school_id=school_id, name=meta.get("name") or f"{meta['stage']}{meta['subject']}知识体系",
                         subject=meta["subject"], stage=meta["stage"], textbook=meta.get("textbook") or "", builtin=builtin,
                         builtin_key=builtin_key, builtin_version=builtin_version)
    s.add(tree)
    s.flush()  # 节点有外键指向知识树，先写入知识树
    count = 0

    def walk(items: list[dict[str, Any]], parent: KnowledgeNode | None, level: int) -> None:
        nonlocal count
        for i, it in enumerate(items):
            name = str(it.get("name") or "").strip()
            if not name:
                raise TreeImportError("存在没有名称的知识点")
            children = it.get("children") or []
            node = KnowledgeNode(
                id="n" + uuid.uuid4().hex[:12], tree_id=tree.id, parent_id=parent.id if parent else None, name=name[:128],
                path=(parent.path + PATH_SEP if parent else "") + name, level=level, seq=count, is_leaf=not children,
                aliases=[str(a).strip() for a in it.get("aliases") or [] if str(a).strip()],
            )
            s.add(node)
            count += 1
            walk(children, node, level + 1)

    walk(nodes, None, 1)
    tree.node_count = count
    s.flush()
    return tree


def seed_builtin(s: Session, school_id: str) -> tuple[int, int]:
    """同步内置知识树（app/data/knowledge/*.json）：新增缺少的、替换内容有变化的、删除已不存在的。
    学校导入的正式知识树不受影响。返回 (新增或更新数, 删除数)。"""
    files = {f.stem: f for f in sorted(BUILTIN_DIR.glob("*.json"))}
    existing = list(s.scalars(select(KnowledgeTree).where(KnowledgeTree.school_id == school_id, KnowledgeTree.builtin.is_(True))))
    by_key = {t.builtin_key: t for t in existing if t.builtin_key}
    removed = 0
    for t in existing:
        # 早期版本没有 builtin_key，或来源文件已删除
        if not t.builtin_key or t.builtin_key not in files:
            delete_tree(s, t)
            removed += 1
    changed = 0
    for key, f in files.items():
        content = f.read_text(encoding="utf-8")
        version = hashlib.md5(content.encode()).hexdigest()[:12]
        old = by_key.get(key)
        if old is not None and old.builtin_version == version:
            continue
        if old is not None:
            delete_tree(s, old)
        meta, nodes = parse_json(content)
        import_tree(s, school_id, meta, nodes, builtin=True, builtin_key=key, builtin_version=version)
        changed += 1
    s.commit()
    return changed, removed


def delete_tree(s: Session, tree: KnowledgeTree) -> None:
    s.execute(delete(KnowledgeNode).where(KnowledgeNode.tree_id == tree.id))
    s.delete(tree)


# ---------------- 查询 ----------------

def pick_tree(s: Session, school_id: str, meta: dict | None) -> KnowledgeTree | None:
    """按试卷学科、学段选知识树：正式导入的优先于内置示例，教材版本一致的优先，其次最新导入的。"""
    m = meta or {}
    subject, stage = m.get("subject"), m.get("stage")
    if not subject or not stage:
        return None
    trees = list(s.scalars(select(KnowledgeTree).where(
        KnowledgeTree.school_id == school_id, KnowledgeTree.subject == subject, KnowledgeTree.stage == stage)))
    if not trees:
        return None
    book = normalize(m.get("textbook") or "")[:4]
    return min(trees, key=lambda t: (t.builtin, not (book and book in normalize(t.textbook)), -t.created_at.timestamp()))


def leaves(s: Session, tree_id: str) -> list[KnowledgeNode]:
    return list(s.scalars(select(KnowledgeNode).where(KnowledgeNode.tree_id == tree_id, KnowledgeNode.is_leaf.is_(True))
                          .order_by(KnowledgeNode.seq)))


@dataclass
class NodeMatch:
    node: KnowledgeNode
    score: float


def match_score(query: str, node: KnowledgeNode) -> float:
    """知识点名称与节点的匹配度：名称 / 别名的字面相似度，完全相同或互相包含时加分。"""
    q = normalize(query)
    best = 0.0
    for cand in [node.name, *node.aliases]:
        c = normalize(re.sub(r"^\d+(\.\d+)*\s*", "", cand))
        if not c or not q:
            continue
        if q == c:
            return 1.0
        score = lexical(q, c)
        if q in c or c in q:
            score = max(score, 0.85 * min(len(q), len(c)) / max(len(q), len(c)) + 0.15)
        best = max(best, score)
    return best


def search_nodes(nodes: list[KnowledgeNode], query: str, limit: int = 8, min_score: float = 0.2) -> list[NodeMatch]:
    matches = [NodeMatch(n, round(match_score(query, n), 4)) for n in nodes]
    matches = [m for m in matches if m.score >= min_score]
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:limit]


def tree_json(s: Session, tree: KnowledgeTree) -> list[dict[str, Any]]:
    """嵌套结构，供前端展示。"""
    nodes = list(s.scalars(select(KnowledgeNode).where(KnowledgeNode.tree_id == tree.id).order_by(KnowledgeNode.seq)))
    by_id: dict[str, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for n in nodes:
        d = {"id": n.id, "name": n.name, "aliases": n.aliases, "children": []}
        by_id[n.id] = d
        (by_id[n.parent_id]["children"] if n.parent_id in by_id else roots).append(d)
    return roots


def resolve_kps(s: Session, job_id: str, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """老师编辑的知识点：id 为知识树节点的直接采用；按名称能在本卷知识树中精确找到的归入节点；
    其余作为树外知识点，按「学科 + 名称」生成 id。"""
    from .db import ParseJob
    from .pipeline.knowledge import kp_id

    job = s.get(ParseJob, job_id)
    meta = (job.meta if job else None) or {}
    tree = pick_tree(s, job.school_id, meta) if job else None
    all_nodes = list(s.scalars(select(KnowledgeNode).where(KnowledgeNode.tree_id == tree.id))) if tree else []
    out: dict[str, dict[str, Any]] = {}
    for r in refs:
        name = str(r.get("name") or "").strip()
        node = s.get(KnowledgeNode, str(r.get("id") or ""))
        if node is None and all_nodes and name:
            # 名称（忽略「1.3」等编号）或别名完全一致才归入节点
            best = search_nodes(all_nodes, name, limit=1, min_score=0.99)
            node = best[0].node if best else None
        if node is not None:
            out[node.id] = {"id": node.id, "name": node.name, "path": node.path, "inTree": True}
        elif name:
            fid = kp_id(meta.get("subject") or "", name)
            out[fid] = {"id": fid, "name": name, "path": None, "inTree": False}
    return list(out.values())
