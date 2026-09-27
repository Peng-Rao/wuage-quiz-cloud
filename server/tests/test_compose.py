"""AI 组卷：蓝图校验、选题、赋分与接口。题目直接写库，使用其他测试不用的学段学科，结束后删除。"""

import json
import re
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.compose import apportion, assign_scores
from app.config import get_settings
from app.db import BankQuestion, SessionLocal
from app.main import app
from app.pipeline import llm
from app.services import current_school

META = {"title": "组卷测试高二生物卷", "stage": "高中", "subject": "生物", "grade": "高二"}
ROOT = "必修"
CELL = f"{ROOT} / 细胞代谢"
KPS = {
    "resp": f"{CELL} / 细胞呼吸",
    "photo": f"{CELL} / 光合作用",
    "gene": f"{ROOT} / 遗传 / 基因的分离定律",
}


def _q(qtype: str, stem: str, coef: float, kp: str) -> BankQuestion:
    path = KPS[kp]
    return BankQuestion(
        id="c" + uuid.uuid4().hex[:20], school_id=current_school(), source_job_id="composejob", source_draft_id=uuid.uuid4().hex,
        type=qtype, score=5, stem=stem, options=[], coef=coef, images=[], meta=META,
        knowledge_points=[{"id": "n-" + kp, "name": path.split(" / ")[-1], "path": path, "inTree": True}],
    )


@pytest.fixture(scope="module")
def client():
    # 题干互不相似，避免被当作重复题排除
    words = ["线粒体", "叶绿体", "酶活性", "温度", "氧气浓度", "二氧化碳", "显性性状", "杂交实验", "豌豆", "酵母菌",
             "乳酸", "光照强度", "色素提取", "测交", "孟德尔", "有氧呼吸", "无氧呼吸", "暗反应", "光反应", "基因型"]
    rows, n = [], 0
    for qtype, count, coefs in (("单选题", 6, [0.15 + 0.1 * i for i in range(6)]), ("解答题", 4, [0.3 + 0.15 * i for i in range(4)])):
        for i in range(count):
            for kp in KPS:
                a, b, c = words[n % 20], words[(n * 7 + 3) % 20], words[(n * 13 + 5) % 20]
                rows.append(_q(qtype, f"{a}与{b}在{c}条件下的变化规律（{n}）", coefs[i], kp))
                n += 1
    with SessionLocal() as s:
        s.add_all(rows)
        s.commit()
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"username": "test-admin", "password": "test-password-123"}).status_code == 200
        yield c
    with SessionLocal() as s:
        s.execute(delete(BankQuestion).where(BankQuestion.source_job_id == "composejob"))
        s.commit()


@pytest.fixture
def llm_reply(monkeypatch):
    """启用大模型；reply(system) 返回蓝图（可按清单中的编号构造）。"""
    s = get_settings()
    monkeypatch.setattr(s, "llm_base_url", "https://llm.test/v1")
    monkeypatch.setattr(s, "llm_api_key", "k")
    monkeypatch.setattr(s, "llm_model", "qwen-test")
    seen: dict = {}

    def install(reply):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen["system"], seen["user"] = body["messages"][0]["content"], body["messages"][1]["content"]
            content = json.dumps(reply(seen["system"]), ensure_ascii=False)
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        monkeypatch.setattr(llm, "transport", httpx.MockTransport(handler))
        return seen
    return install


def code_of(system: str, path: str) -> str:
    return re.search(r"^(k\d+)：" + re.escape(path) + "（", system, re.M).group(1)


def _post(client, text="高二学生，期中复习", **kw):
    body = {"stage": "高中", "subject": "生物", "total": 100, "difficulty": 0.45,
            "messages": [{"role": "user", "content": text}], **kw}
    return client.post("/api/compose", json=body)


def _ids(res: dict) -> list[str]:
    return [it["question"]["id"] for sec in res["sections"] for it in sec["items"]]


def test_apportion_exact_and_min_one():
    assert apportion([1, 1, 1], 10) in ([4, 3, 3], [3, 4, 3], [3, 3, 4])
    assert sum(apportion([12.3, 15.1, 17.9], 47)) == 47
    assert apportion([5, 1], 2) == [1, 1]
    with pytest.raises(ValueError):
        apportion([1, 1, 1], 2)


def test_assign_scores_uniform_sections_and_exact_total():
    qs = [BankQuestion(id=f"s{i}", type="单选题", coef=0.3) for i in range(8)]
    hard = [BankQuestion(id=f"h{i}", type="解答题", coef=c) for i, c in enumerate([0.4, 0.6, 0.9])]
    for total in (60, 100, 150, 23):
        scores = assign_scores([("单选题", qs), ("解答题", hard)], total)
        assert sum(scores.values()) == total
        assert len({scores[q.id] for q in qs}) == 1  # 同一大题分值相同
    scores = assign_scores([("单选题", qs), ("解答题", hard)], 150)
    assert scores["h2"] >= scores["h0"]  # 越难分值越高
    # 没有解答题：余数由最后一个大题承担，总分仍准确
    fill = [BankQuestion(id=f"f{i}", type="填空题", coef=0.4) for i in range(3)]
    assert sum(assign_scores([("单选题", qs), ("填空题", fill)], 57).values()) == 57


def test_compose_without_llm_uses_keywords(client):
    r = _post(client, "高二学生，细胞呼吸比较薄弱，期中复习")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["ai"] is False and "未配置大模型" in res["reply"]
    assert res["total"] == 100 == sum(it["score"] for sec in res["sections"] for it in sec["items"])
    assert sum(sec["score"] for sec in res["sections"]) == 100
    ids = _ids(res)
    assert len(ids) == len(set(ids))
    assert [f["name"] for f in res["focus"]] == ["细胞呼吸"]
    kps = [it["question"]["knowledgePoints"][0]["name"] for sec in res["sections"] for it in sec["items"]]
    # 总分 100 默认 7 道单选、4 道解答；细胞呼吸的单选只有 6 道，全部选入，另 1 道从其他知识点补充并提示
    assert (len(kps), kps.count("细胞呼吸")) == (11, 10)
    assert res["gaps"] == ["单选题：与重点知识点相关的题不足，有 1 道从其他知识点补充"]
    types = [sec["type"] for sec in res["sections"]]
    assert types == ["单选题", "解答题"]
    for sec in res["sections"]:
        coefs = [it["question"]["coef"] for it in sec["items"]]
        assert coefs == sorted(coefs)  # 大题内由易到难


def test_compose_with_llm_blueprint(client, llm_reply):
    seen = llm_reply(lambda system: {
        "reply": "侧重光合作用，适当降低难度。", "title": "光合作用专项", "total": 60, "difficulty": 0.3,
        "sections": [{"type": "单选题", "count": 5}, {"type": "解答题", "count": 2}, {"type": "多选题", "count": 3}],
        "focus": [{"id": code_of(system, KPS["photo"]), "weight": 3}, {"id": "k999", "weight": 2}],
        "avoid": [code_of(system, KPS["gene"])],
    })
    r = _post(client, "光合作用专项，简单一点，满分 60")
    assert r.status_code == 200, r.text
    res = r.json()
    assert "光合作用（" in seen["system"] and "细胞代谢（" in seen["system"]  # 清单含上级知识点
    assert f"{ROOT}（" not in seen["system"]  # 不含最顶层模块
    assert "总分 100 分" in seen["user"] and "老师：光合作用专项" in seen["user"]
    assert res["ai"] is True and res["title"] == "光合作用专项" and res["reply"].startswith("侧重光合作用")
    assert res["total"] == 60 and res["difficulty"] == 0.3
    # 题库中没有多选题，该题型被忽略；不存在的编号被忽略
    assert [(sec["type"], len(sec["items"])) for sec in res["sections"]] == [("单选题", 5), ("解答题", 2)]
    assert [(f["name"], f["weight"], f["count"]) for f in res["focus"]] == [("光合作用", 3, 7)]
    assert sum(it["score"] for sec in res["sections"] for it in sec["items"]) == 60


def test_compose_avoid_and_gaps(client, llm_reply):
    llm_reply(lambda system: {
        "sections": [{"type": "解答题", "count": 8}],
        "focus": [{"id": code_of(system, KPS["resp"]), "weight": 2}],
        "avoid": [code_of(system, KPS["gene"])],
    })
    res = _post(client, "只要解答题，不考遗传").json()
    kps = {it["question"]["knowledgePoints"][0]["name"] for sec in res["sections"] for it in sec["items"]}
    assert "基因的分离定律" not in kps
    # 细胞呼吸的解答题只有 4 道，其余从光合作用补充
    assert len(_ids(res)) == 8
    assert any("有 4 道从其他知识点补充" in g for g in res["gaps"])


def test_compose_llm_failure_falls_back(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_base_url", "https://llm.test/v1")
    monkeypatch.setattr(s, "llm_api_key", "k")
    monkeypatch.setattr(s, "llm_model", "qwen-test")
    monkeypatch.setattr(llm, "transport", httpx.MockTransport(lambda _: httpx.Response(500)))
    res = _post(client, "光合作用").json()
    assert res["ai"] is False and res["reply"].startswith("大模型暂时不可用")
    assert [f["name"] for f in res["focus"]] == ["光合作用"]


def test_compose_without_knowledge_points_warns(client):
    with SessionLocal() as s:
        s.add(BankQuestion(id="c" + uuid.uuid4().hex[:20], school_id=current_school(), source_job_id="composejob",
                           source_draft_id=uuid.uuid4().hex, type="单选题", score=3, stem="未标注知识点的题", options=[],
                           coef=0.4, images=[], knowledge_points=[], meta={**META, "subject": "地理"}))
        s.commit()
    res = _post(client, "侧重地图", subject="地理").json()
    assert len(_ids(res)) == 1 and res["total"] == 100
    assert res["gaps"][0].startswith("题库中的题目尚未标注知识点")


def test_compose_errors(client):
    r = client.post("/api/compose", json={"stage": "小学", "subject": "科学", "messages": [{"role": "user", "content": "出卷"}]})
    assert r.status_code == 400 and "还没有小学科学的题目" in r.json()["message"]
    assert client.post("/api/compose", json={"stage": "高中", "subject": "生物", "messages": []}).status_code == 422
