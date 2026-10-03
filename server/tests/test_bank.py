"""校本题库选题（知识点筛选）与试卷库。数据直接写库，不经过解析流水线。"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import BankQuestion, DraftQuestion, KnowledgeNode, KnowledgeTree, ParseJob, SessionLocal
from app.main import app
from app.services import current_school

MATH_META = {"title": "题库测试高一期中数学卷", "stage": "高中", "subject": "数学", "grade": "高一", "paper_type": "期中考试",
             "region": "北京", "school_year": "2026—2027", "textbook": ""}
CHEM_META = {"title": "题库测试初三化学月考卷", "stage": "初中", "subject": "化学", "grade": "初三", "paper_type": "月考",
             "region": "厦门", "school_year": "2024—2025", "textbook": "", "school": "厦门双十中学"}


def _node(s, tree: KnowledgeTree, name: str) -> KnowledgeNode:
    return s.scalars(select(KnowledgeNode).where(KnowledgeNode.tree_id == tree.id, KnowledgeNode.name == name)).one()


def _kp(n: KnowledgeNode, *, stale_id: bool = False) -> dict:
    # stale_id：模拟内置知识树更新后节点 id 已变，只能按路径归入
    return {"id": "n-old" if stale_id else n.id, "name": n.name, "path": n.path, "inTree": True}


def _paper(s, meta: dict, questions: list[dict], drafts: int | None = None) -> str:
    job = ParseJob(id=uuid.uuid4().hex[:16], school_id=current_school(), file_name=meta["title"] + ".pdf", file_count=1,
                   file_size=1, file_type="pdf", file_keys=[], options={}, status="done", progress=100, stages=[],
                   meta=meta, warnings=[])
    s.add(job)
    s.flush()  # 草稿题有外键指向任务
    for i, q in enumerate(questions, 1):
        did = "q" + uuid.uuid4().hex[:20]
        s.add(DraftQuestion(id=did, job_id=job.id, no=i, type=q["type"], score=q["score"], page=1, stem=q["stem"],
                            options=[], knowledge_points=q.get("kps", []), coef=q["coef"], confidence=1, status="saved"))
        s.add(BankQuestion(id="k" + uuid.uuid4().hex[:20], school_id=job.school_id, source_job_id=job.id, source_draft_id=did,
                           type=q["type"], score=q["score"], stem=q["stem"], options=[], knowledge_points=q.get("kps", []),
                           coef=q["coef"], images=[], meta=meta, source_file_name=job.file_name, source_no=i, source_page=1))
    for i in range(len(questions), drafts or 0):  # 未入库的题
        s.add(DraftQuestion(id="q" + uuid.uuid4().hex[:20], job_id=job.id, no=i + 1, type="解答题", score=10, page=2,
                            stem="未入库", options=[], knowledge_points=[], coef=0.5, confidence=1))
    return job.id


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"username": "test-admin", "password": "test-password-123"}).status_code == 200
        yield c


@pytest.fixture(scope="module")
def data(client):
    with SessionLocal() as s:
        tree = s.scalars(select(KnowledgeTree).where(KnowledgeTree.school_id == current_school(), KnowledgeTree.builtin.is_(True),
                                                     KnowledgeTree.stage == "高中", KnowledgeTree.subject == "数学")).one()
        subset, inter, deriv = _node(s, tree, "子集"), _node(s, tree, "交集与并集"), _node(s, tree, "导数研究极值")
        module = s.get(KnowledgeNode, subset.parent_id)
        math = _paper(s, MATH_META, [
            {"type": "单选题", "score": 5, "stem": "集合 A 的子集个数为（ ）", "coef": 0.2, "kps": [_kp(subset)]},
            {"type": "单选题", "score": 5, "stem": "求 A ∩ B", "coef": 0.45, "kps": [_kp(inter, stale_id=True)]},
            {"type": "解答题", "score": 12, "stem": "讨论函数的极值", "coef": 0.8, "kps": [_kp(deriv), _kp(subset)]},
        ], drafts=4)
        chem = _paper(s, CHEM_META, [
            {"type": "单选题", "score": 2, "stem": "下列变化属于化学变化的是", "coef": 0.1},
        ])
        s.commit()
        return {"math": math, "chem": chem, "tree": tree.id, "subset": subset.id, "module": module.id,
                "root": module.parent_id, "deriv": deriv.id}


def _search(client, **params) -> dict:
    r = client.get("/api/bank/questions", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_search_by_knowledge_node(client, data):
    base = {"stage": "高中", "subject": "数学", "paperId": data["math"]}
    # 叶子节点：按 id 命中两题
    got = _search(client, nodeId=data["subset"], **base)
    assert got["total"] == 2 and {q["stem"][:2] for q in got["items"]} == {"集合", "讨论"}
    # 上级节点含下级；节点 id 已失效的题按路径归入
    assert _search(client, nodeId=data["module"], **base)["total"] == 3
    assert _search(client, nodeId=data["deriv"], **base)["total"] == 1
    assert _search(client, nodeId="missing", **base)["total"] == 0


def test_search_filters_and_sort(client, data):
    base = {"paperId": data["math"]}
    assert _search(client, type="解答题", **base)["total"] == 1
    assert [q["coef"] for q in _search(client, diff="适中", **base)["items"]] == [0.45]
    assert [q["coef"] for q in _search(client, sort="hard", **base)["items"]] == [0.8, 0.45, 0.2]
    # 综合排序：卷内按题号
    items = _search(client, **base)["items"]
    assert [q["source"]["no"] for q in items] == [1, 2, 3]
    assert items[0]["paperId"] == data["math"] and "题库测试高一期中数学卷" in items[0]["source"]["label"]
    assert items[0]["knowledgePoints"][0]["name"] == "子集"
    # 分页
    page = _search(client, limit=2, offset=2, **base)
    assert page["total"] == 3 and len(page["items"]) == 1
    # 试卷类型、年份、关键词
    assert _search(client, paperType="期中,期末", **base)["total"] == 3
    assert _search(client, paperType="月考", **base)["total"] == 0
    assert _search(client, year="2026", **base)["total"] == 3
    assert _search(client, year="<2026", **base)["total"] == 0
    assert _search(client, year="<2025", paperId=data["chem"])["total"] == 1
    assert _search(client, q="极值", **base)["total"] == 1
    assert _search(client, q="交集与并集", **base)["total"] == 1  # 知识点名称
    assert _search(client, stage="初中", subject="化学", q="化学变化")["total"] == 1
    assert client.get("/api/bank/questions", params={"diff": "很难"}).status_code == 422


def test_knowledge_counts(client, data):
    r = client.get("/api/bank/knowledge-counts", params={"treeId": data["tree"]})
    assert r.status_code == 200
    counts = r.json()
    assert counts[data["subset"]] >= 2 and counts[data["deriv"]] >= 1
    # 一道题同时标了模块下的两个知识点，上级只计一次
    assert counts[data["module"]] >= 3 and counts[data["root"]] >= counts[data["module"]]
    assert client.get("/api/bank/knowledge-counts", params={"treeId": "missing"}).status_code == 404


def test_paper_library(client, data):
    page = client.get("/api/papers", params={"q": "题库测试"}).json()
    assert page["total"] == 2
    math = next(p for p in page["items"] if p["id"] == data["math"])
    assert math["title"] == "题库测试高一期中数学卷" and math["meta"]["grade"] == "高一"
    assert math["questionCount"] == 3 and math["sourceQuestionCount"] == 4 and math["totalScore"] == 22
    assert math["typeCounts"] == {"单选题": 2, "解答题": 1}
    assert math["avgCoef"] == round((0.2 * 5 + 0.45 * 5 + 0.8 * 12) / 22, 2)

    # 按年级筛选；年级维度的 facet 不受年级筛选影响，其余维度随之收窄
    page = client.get("/api/papers", params={"q": "题库测试", "grade": "初三"}).json()
    assert [p["id"] for p in page["items"]] == [data["chem"]]
    grades = {f["name"]: f["count"] for f in page["facets"]["grades"]}
    assert grades == {"高一": 1, "初三": 1}
    assert {f["name"] for f in page["facets"]["subjects"]} == {"化学"}

    # 按学校筛选；名称中没有学校的记为「未分类」
    page = client.get("/api/papers", params={"q": "题库测试", "school": "厦门双十中学"}).json()
    assert [p["id"] for p in page["items"]] == [data["chem"]]
    assert {f["name"]: f["count"] for f in page["facets"]["schools"]} == {"厦门双十中学": 1, "未分类": 1}

    detail = client.get(f"/api/papers/{data['math']}").json()
    assert [q["stem"][:2] for q in detail["questions"]] == ["集合", "求 ", "讨论"]
    assert client.get("/api/papers/missing").status_code == 404


def test_meta_edit_syncs_to_bank(client, data):
    meta = dict(MATH_META, grade="高二", paper_type="期末考试")
    assert client.put(f"/api/parse-jobs/{data['math']}/meta", json=meta).status_code == 200
    assert _search(client, paperId=data["math"], paperType="期末")["total"] == 3
    assert client.get(f"/api/papers/{data['math']}").json()["meta"]["grade"] == "高二"


# ---------------- 重复入库 ----------------

def _upload_job(s, meta: dict, stems: list[str], data: bytes) -> tuple[str, list[str]]:
    """带真实文件的已解析任务（未入库），返回任务 id 与草稿题 id。"""
    from app.services import new_job
    from app.schemas import ParseOptions
    from app.storage import get_store, new_upload_key

    store = get_store()
    key = new_upload_key("paper.pdf")
    store.put(key, data)
    job = new_job(s, [key], [meta["title"] + ".pdf"], ParseOptions())
    job.status, job.meta = "done", meta
    s.flush()
    ids = []
    for i, stem in enumerate(stems, 1):
        did = "q" + uuid.uuid4().hex[:20]
        s.add(DraftQuestion(id=did, job_id=job.id, no=i, type="单选题", score=5, page=1, stem=stem, options=[],
                            knowledge_points=[], coef=0.3, confidence=1))
        ids.append(did)
    s.commit()
    return job.id, ids


DUP_META = dict(MATH_META, title="重复入库测试：高一数学第一次月考试卷", school_year="2025—2026")
DUP_STEMS = [f"重复入库测试第 {i} 题：已知函数 f(x) = x^{i} + {i}x，求 f({i}) 的值与函数的单调区间" for i in range(1, 5)]


def _commit(client, job_id: str, ids: list[str], force: bool = False) -> dict:
    r = client.post(f"/api/parse-jobs/{job_id}/commit", json={"questionIds": ids, "force": force})
    assert r.status_code == 200, r.text
    return r.json()


def test_same_file_is_duplicate_paper(client):
    data = b"%PDF-1.4 duplicate-paper-test"
    with SessionLocal() as s:
        first, ids1 = _upload_job(s, DUP_META, DUP_STEMS, data)
        second, ids2 = _upload_job(s, dict(DUP_META, title="重复入库测试：另起的名字但文件相同"), ["完全不同的题干内容用于测试文件指纹"], data)
    r = _commit(client, first, ids1)
    assert r["savedCount"] == 4 and r["duplicatePaper"] is None
    # 同一份试卷重新保存（如修改后）不算重复
    assert _commit(client, first, ids1[:2])["savedCount"] == 2

    r = _commit(client, second, ids2)
    assert r["savedCount"] == 0 and r["duplicatePaper"]["id"] == first and r["duplicatePaper"]["reason"] == "same_file"
    assert client.get(f"/api/papers/{second}").status_code == 404
    # 确认后强制保存
    assert _commit(client, second, ids2, force=True)["savedCount"] == 1


def test_same_title_and_most_questions(client):
    with SessionLocal() as s:
        same_title, ids1 = _upload_job(s, DUP_META, ["同名试卷测试：一道全新的题目，题干与已有题目都不同"], b"%PDF same-title")
        rescan, ids2 = _upload_job(s, dict(DUP_META, title="扫描版：另一个名称的试卷"), DUP_STEMS[:3] + ["扫描版新增的一道题，与题库中的题都不相同"],
                                   b"%PDF rescan")
    r = _commit(client, same_title, ids1)
    assert r["duplicatePaper"]["reason"] == "same_title" and r["savedCount"] == 0
    r = _commit(client, rescan, ids2)
    assert r["duplicatePaper"]["reason"] == "most_questions" and r["savedCount"] == 0


def test_duplicate_questions_are_skipped(client):
    with SessionLocal() as s:
        job, ids = _upload_job(s, dict(DUP_META, title="重复题测试：高一数学单元练习卷"),
                               [DUP_STEMS[0], "单元练习卷中新出现的一道题，题库中没有相同的题目"], b"%PDF skip-questions")
    r = _commit(client, job, ids)
    assert r["duplicatePaper"] is None and r["savedIds"] == [ids[1]]
    assert [(x["questionId"], x["no"]) for x in r["skipped"]] == [(ids[0], 1)]
    assert r["skipped"][0]["score"] >= 0.85 and "重复入库测试" in r["skipped"][0]["source"]
    # 老师确认后单独强制保存被跳过的题
    assert _commit(client, job, [ids[0]], force=True)["savedIds"] == [ids[0]]


def test_remove_paper(client):
    with SessionLocal() as s:
        job, ids = _upload_job(s, dict(DUP_META, title="移出试卷库测试：高一数学周练试卷"),
                               ["移出试卷库测试的题目一，题干内容独一无二"], b"%PDF remove")
    assert _commit(client, job, ids)["savedCount"] == 1
    assert client.delete(f"/api/papers/{job}").status_code == 204
    assert client.get(f"/api/papers/{job}").status_code == 404
    assert client.get(f"/api/parse-jobs/{job}/questions").json()[0]["status"] == "draft"
    assert client.delete(f"/api/papers/{job}").status_code == 404
    # 移出后可重新保存
    assert _commit(client, job, ids)["savedCount"] == 1


# ---------------- 章节选题与筛选 ----------------

def test_builtin_chapters_map_to_knowledge_tree():
    """内置教材目录中的知识点都应能在同学段学科的内置知识树中找到（树外的个别名称按名称匹配）。"""
    import json
    from pathlib import Path

    from app.chapters import catalog

    kdir = Path(__file__).parent.parent / "app" / "data" / "knowledge"
    for v in catalog():
        tree = json.loads((kdir / f"{v['stage']}{v['subject']}.json").read_text(encoding="utf-8"))
        names: set[str] = set()

        def walk(ns):  # noqa: ANN001
            for n in ns:
                names.add(n["name"])
                walk(n.get("children", []))

        walk(tree["nodes"])
        wanted = {k for b in v["books"] for c in b["chapters"] for x in c["sections"] for k in x["knowledge"]}
        assert wanted - names <= {"数学归纳法"}, (v["version"], wanted - names)
        ids = [x["id"] for b in v["books"] for c in b["chapters"] for x in [c, *c["sections"]]]
        assert len(ids) == len(set(ids))


def test_chapter_filter_counts_and_more_filters(client):
    books = client.get("/api/chapters", params={"stage": "高中", "subject": "数学"}).json()
    assert books[0]["name"] == "人教A版"
    b1 = books[0]["books"][0]
    ch1 = b1["chapters"][0]
    sec = {x["name"]: x["id"] for x in ch1["sections"]}
    assert b1["name"] == "必修 第一册" and "1.2 集合间的基本关系" in sec
    assert client.get("/api/chapters", params={"stage": "高中", "subject": "信息技术"}).json() == []

    meta = dict(MATH_META, title="章节筛选测试：浙江杭州高一下学期期末数学", region="浙江 · 杭州", grade="高一",
                school_year="2025—2026 下", paper_type="期末考试")
    with SessionLocal() as s:
        tree = s.scalars(select(KnowledgeTree).where(KnowledgeTree.school_id == current_school(), KnowledgeTree.builtin.is_(True),
                                                     KnowledgeTree.stage == "高中", KnowledgeTree.subject == "数学")).one()
        subset, compl, deriv = _node(s, tree, "子集"), _node(s, tree, "补集"), _node(s, tree, "导数研究极值")
        pid = _paper(s, meta, [
            {"type": "单选题", "score": 5, "stem": "章节筛选测试：子集个数", "coef": 0.2, "kps": [_kp(subset)]},
            {"type": "单选题", "score": 5, "stem": "章节筛选测试：补集运算", "coef": 0.3, "kps": [_kp(compl, stale_id=True)]},
            {"type": "解答题", "score": 12, "stem": "章节筛选测试：极值", "coef": 0.7, "kps": [_kp(deriv)]},
        ])
        s.commit()
        ids = {"subset": subset.id, "compl": compl.id, "deriv": deriv.id}

    base = {"paperId": pid}
    assert _search(client, chapterId=sec["1.2 集合间的基本关系"], **base)["total"] == 1
    assert _search(client, chapterId=sec["1.3 集合的基本运算"], **base)["total"] == 1  # 按路径中的名称匹配
    assert _search(client, chapterId=ch1["id"], **base)["total"] == 2  # 章 = 各节之和
    assert _search(client, chapterId="missing", **base)["total"] == 0
    # 知识点多选：含任一即可
    assert _search(client, nodeId=f"{ids['subset']},{ids['deriv']}", **base)["total"] == 2
    # 地区（省级）、年级、学期、场景（试卷类型或名称）
    assert _search(client, region="浙江", **base)["total"] == 3
    assert _search(client, region="北京", **base)["total"] == 0
    assert _search(client, grade="高一", term="下", **base)["total"] == 3
    assert _search(client, term="上", **base)["total"] == 0
    assert _search(client, paperType="期末", **base)["total"] == 3

    counts = client.get("/api/bank/chapter-counts", params={"bookId": b1["id"]}).json()
    assert counts[sec["1.2 集合间的基本关系"]] >= 1 and counts[ch1["id"]] >= 2
    assert client.get("/api/bank/chapter-counts", params={"bookId": "missing"}).status_code == 404

    facets = client.get("/api/bank/facets", params={"stage": "高中", "subject": "数学"}).json()
    assert "浙江" in {f["name"] for f in facets["regions"]} and "2025" in {f["name"] for f in facets["years"]}

    page = client.get("/api/papers", params={"q": "章节筛选测试", "category": "期中,期末"}).json()
    assert [p["id"] for p in page["items"]] == [pid]
    assert client.get("/api/papers", params={"q": "章节筛选测试", "category": "高考"}).json()["total"] == 0
