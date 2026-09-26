import json
import time

import httpx
import pytest

from app.config import get_settings
from app.pipeline import llm
from app.pipeline.classify import rule_title
from app.pipeline.knowledge import _clean, kp_id

from .fixtures import TITLE, make_exam_pdf
from .test_api import client, upload, wait_done  # noqa: F401


def test_title_and_kp_helpers():
    assert rule_title(f"绝密★启用前\n{TITLE}\n数 学\n本试卷共 4 页") == TITLE
    assert rule_title("试卷第1页（共7页）\n一、单选题") == ""
    assert _clean("1．集合的基本运算。") == "集合的基本运算"
    assert kp_id("数学", "函数") == kp_id("数学", "函数") != kp_id("物理", "函数")


@pytest.fixture
def paper(client):  # noqa: F811
    key = upload(client, "出处测试.pdf", make_exam_pdf())
    job = client.post("/api/parse-jobs", json={"fileKeys": [key], "fileNames": ["出处测试.pdf"],
                                               "options": {"dedupe": False}}).json()
    return wait_done(client, job["id"])


def test_question_source(client, paper):  # noqa: F811
    assert paper["meta"]["title"] == TITLE
    # 未配置大模型：知识点阶段跳过并说明原因
    kn = next(s for s in paper["stages"] if s["stage"] == "knowledge")
    assert (kn["status"], kn["note"]) == ("skipped", "未配置大模型")
    qs = client.get(f"/api/parse-jobs/{paper['id']}/questions").json()
    src = qs[2]["source"]
    assert src["label"] == f"2026—2027 上 · 北京 · 海淀 · 高一期中考试《{TITLE}》第 3 题"
    assert (src["no"], src["page"], src["fileName"]) == (3, 1, "出处测试.pdf")

    # 修改分类后出处随之更新
    meta = dict(paper["meta"], region="北京 · 西城", title="")
    client.put(f"/api/parse-jobs/{paper['id']}/meta", json=meta)
    q = client.get(f"/api/parse-jobs/{paper['id']}/questions").json()[0]
    assert q["source"]["label"] == "2026—2027 上 · 北京 · 西城 · 高一期中考试《出处测试》第 1 题"

    # 入库后相似题结果带出处
    client.post(f"/api/parse-jobs/{paper['id']}/commit", json={"questionIds": [q["id"]]})
    r = client.post("/api/similar/search", json={"text": q["stem"], "limit": 20}).json()
    mine = next(x for x in r if x["origin"] and x["origin"]["fileName"] == "出处测试.pdf")
    assert mine["origin"]["no"] == 1 and "第 1 题" in mine["origin"]["label"]


def test_tag_knowledge_endpoint(client, paper, monkeypatch):  # noqa: F811
    s = get_settings()
    for k, v in {"llm_base_url": "https://llm.test/v1", "llm_api_key": "k", "llm_model": "m"}.items():
        monkeypatch.setattr(s, k, v)
    batches = []

    def handler(req: httpx.Request) -> httpx.Response:
        user = json.loads(req.content)["messages"][1]["content"]
        nos = [int(x) for x in __import__("re").findall(r"第 (\d+) 题", user)]
        batches.append(nos)
        items = [{"no": n, "kps": ["1．集合的基本运算", "集合的基本运算", "一元二次不等式", "补集", "交集"]} for n in nos]
        content = json.dumps({"questions": items}, ensure_ascii=False)
        body = f'data: {json.dumps({"choices": [{"delta": {"content": content}}]})}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handler))
    r = client.post(f"/api/parse-jobs/{paper['id']}/tag-knowledge")
    assert r.status_code == 202
    assert next(x for x in r.json()["stages"] if x["stage"] == "knowledge")["status"] == "running"
    deadline = time.time() + 10
    while time.time() < deadline:
        st = next(x for x in client.get(f"/api/parse-jobs/{paper['id']}").json()["stages"] if x["stage"] == "knowledge")
        if st["status"] != "running":
            break
        time.sleep(0.05)
    assert st["status"] == "done" and st["note"].startswith("补标 9 题")
    assert batches == [list(range(1, 10))]
    qs = client.get(f"/api/parse-jobs/{paper['id']}/questions").json()
    # 去掉编号、去重、最多 3 个，同名知识点 id 一致
    assert [k["name"] for k in qs[0]["knowledgePoints"]] == ["集合的基本运算", "一元二次不等式", "补集"]
    assert qs[0]["knowledgePoints"][0]["id"] == qs[5]["knowledgePoints"][0]["id"]
    r = client.post(f"/api/parse-jobs/{paper['id']}/tag-knowledge")
    assert r.status_code == 400 and "都已标注" in r.json()["message"]


def test_patch_only_marks_real_changes(client, paper):  # noqa: F811
    q = client.get(f"/api/parse-jobs/{paper['id']}/questions").json()[0]
    assert q["answerSource"] == "paper"
    # 编辑弹窗带上全部字段，但答案未变：来源保持原卷
    r = client.patch(f"/api/draft-questions/{q['id']}", json={
        "stem": q["stem"] + "。", "answer": q["answer"], "analysis": q["analysis"],
        "knowledgePoints": [{"id": "kp_随便", "name": "集合的基本运算"}, {"id": "x", "name": " 集合的基本运算 "}],
    }).json()
    assert r["answerSource"] == "paper" and r["confidence"] == 1.0
    assert r["knowledgePoints"] == [{"id": kp_id("数学", "集合的基本运算"), "name": "集合的基本运算"}]
    r = client.patch(f"/api/draft-questions/{q['id']}", json={"answer": "B"}).json()
    assert r["answerSource"] == "manual"
