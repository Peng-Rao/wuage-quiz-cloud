import json

from app.db import SessionLocal
from app.knowledge_tree import match_score, parse_csv, pick_tree

from .test_api import client  # noqa: F401

CSV = """一级,二级,三级,别名
第一章 集合,1.1 集合的概念,元素与集合,
第一章 集合,1.3 集合的运算,交集,交运算|A∩B
第一章 集合,1.3 集合的运算,并集,
第二章 函数,函数的单调性,,单调性
"""


def test_parse_csv_builds_nested_tree():
    nodes = parse_csv(CSV)
    assert [n["name"] for n in nodes] == ["第一章 集合", "第二章 函数"]
    ops = nodes[0]["children"][1]
    assert ops["name"] == "1.3 集合的运算" and [c["name"] for c in ops["children"]] == ["交集", "并集"]
    assert ops["children"][0]["aliases"] == ["A∩B", "交运算"]
    assert nodes[1]["children"][0]["aliases"] == ["单调性"]


def test_match_score_ignores_numbering_and_uses_aliases():
    from app.db import KnowledgeNode
    n = KnowledgeNode(name="1.3 集合的基本运算", aliases=["集合运算"])
    assert match_score("集合的基本运算", n) == 1.0
    assert match_score("集合运算", n) == 1.0
    assert match_score("基本运算", n) > 0.5
    assert match_score("导数", n) < 0.2


def test_tree_api_import_search_pick_delete(client):  # noqa: F811
    trees = client.get("/api/knowledge-trees").json()
    builtin = {(t["stage"], t["subject"]): t for t in trees if t["builtin"]}
    assert ("高中", "数学") in builtin and ("初中", "化学") in builtin

    # CSV 必须提供学科、学段
    r = client.post("/api/knowledge-trees/import", json={"format": "csv", "content": CSV})
    assert r.status_code == 400 and "学科" in r.json()["message"]
    r = client.post("/api/knowledge-trees/import", json={
        "format": "csv", "content": CSV, "name": "校本数学知识树", "subject": "数学", "stage": "高中", "textbook": "人教A版（2019）"})
    assert r.status_code == 200, r.text
    mine = r.json()
    assert mine["nodeCount"] == 8 and not mine["builtin"]

    detail = client.get(f"/api/knowledge-trees/{mine['id']}").json()
    assert detail["nodes"][0]["children"][1]["children"][0]["name"] == "交集"

    # 同学科同学段：正式导入的优先于内置示例
    with SessionLocal() as s:
        assert pick_tree(s, "demo", {"subject": "数学", "stage": "高中", "textbook": "人教A版（2019）"}).id == mine["id"]
    hits = client.get("/api/knowledge/search", params={"q": "交运算", "treeId": mine["id"]}).json()
    assert hits[0]["name"] == "交集" and hits[0]["path"] == "第一章 集合 / 1.3 集合的运算 / 交集"
    assert client.get("/api/knowledge/search", params={"q": "交集"}).json() == []  # 未指定知识树

    bad = client.post("/api/knowledge-trees/import", json={"format": "json", "content": "{"})
    assert bad.status_code == 400 and "JSON" in bad.json()["message"]
    j = client.post("/api/knowledge-trees/import", json={"format": "json", "content": json.dumps(
        {"name": "物理示例", "subject": "物理", "stage": "初中", "nodes": [{"name": "力", "children": [{"name": "重力"}]}]})})
    assert j.json()["nodeCount"] == 2

    assert client.delete(f"/api/knowledge-trees/{mine['id']}").status_code == 204
    assert client.get(f"/api/knowledge-trees/{mine['id']}").status_code == 404
    with SessionLocal() as s:
        assert pick_tree(s, "demo", {"subject": "数学", "stage": "高中"}).builtin
