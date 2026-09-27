"""教材章节目录（章节选题）。

内置目录见 app/data/chapters/*.json（由 scripts/crawl_textbooks.py 从国家中小学智慧教育平台抓取厦门市现用版本生成）：
{stage, subject, version, order, region, books: [{name, grade, edition, chapters: [{name, knowledge?, sections: [{name, knowledge}]}]}]}。
每节（没有节的章为章本身）的 knowledge 为对应的知识点名称（知识树中的节点，可以是叶子或上级节点）；同一学科多个版本时 order 小的为默认。题目不单独标注章节：
按章节筛选时，知识点路径中任一级名称属于该节 knowledge 的题即归入该节，已标注知识点的题无需重新解析。
"""

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .knowledge_tree import PATH_SEP

CHAPTER_DIR = Path(__file__).parent / "data" / "chapters"


def _id(*parts: str) -> str:
    return "c" + hashlib.md5("/".join(parts).encode()).hexdigest()[:10]


def _unique(names: list[str]) -> list[str]:
    """同一级中重名的（如多个「单元学习任务」）依次加「#2」「#3」，保证 id 唯一。"""
    seen: dict[str, int] = {}
    out = []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}#{seen[n]}")
    return out


@dataclass(frozen=True)
class ChapterNode:
    id: str
    name: str
    # 本节（章为所含各节之和）对应的知识点名称
    knowledge: frozenset[str]
    book_id: str


@lru_cache
def catalog() -> list[dict[str, Any]]:
    """全部内置教材，每一级带稳定的 id（由名称路径生成）。"""
    out = []
    files = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(CHAPTER_DIR.glob("*.json"))]
    for d in sorted(files, key=lambda d: (d["stage"], d["subject"], d.get("order", 0))):
        books = []
        for b in d["books"]:
            bid = _id(d["stage"], d["subject"], d["version"], b["name"])
            chapters = []
            for c, ckey in zip(b["chapters"], _unique([c["name"] for c in b["chapters"]])):
                cid = _id(bid, ckey)
                secs = c.get("sections", [])
                sections = [{"id": _id(cid, key), "name": s["name"], "knowledge": s.get("knowledge", [])}
                            for s, key in zip(secs, _unique([s["name"] for s in secs]))]
                chapters.append({"id": cid, "name": c["name"], "knowledge": c.get("knowledge", []), "sections": sections})
            books.append({"id": bid, "name": b["name"], "grade": b.get("grade", ""), "edition": b.get("edition", ""),
                          "chapters": chapters})
        out.append({"stage": d["stage"], "subject": d["subject"], "version": d["version"], "region": d.get("region", ""),
                    "books": books})
    return out


def versions(stage: str, subject: str) -> list[dict[str, Any]]:
    return [{"name": v["version"], "region": v["region"], "books": v["books"]}
            for v in catalog() if v["stage"] == stage and v["subject"] == subject]


@lru_cache
def _nodes() -> dict[str, ChapterNode]:
    out: dict[str, ChapterNode] = {}
    for v in catalog():
        for b in v["books"]:
            for c in b["chapters"]:
                allk: set[str] = set(c["knowledge"])  # 没有节的章直接对应知识点
                for s in c["sections"]:
                    out[s["id"]] = ChapterNode(s["id"], s["name"], frozenset(s["knowledge"]), b["id"])
                    allk |= set(s["knowledge"])
                out[c["id"]] = ChapterNode(c["id"], c["name"], frozenset(allk), b["id"])
    return out


def chapter_node(node_id: str) -> ChapterNode | None:
    return _nodes().get(node_id)


def book(book_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(教材版本, 册)"""
    for v in catalog():
        for b in v["books"]:
            if b["id"] == book_id:
                return v, b
    return None


def kp_names(kp: dict[str, Any]) -> set[str]:
    """知识点及其各级上级的名称。"""
    names = {kp.get("name") or ""}
    if kp.get("path"):
        names |= set(kp["path"].split(PATH_SEP))
    return names - {""}


def kps_match(kps: list[dict[str, Any]], knowledge: frozenset[str]) -> bool:
    return any(kp_names(k) & knowledge for k in kps or [])
