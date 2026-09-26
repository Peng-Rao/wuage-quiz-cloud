import json
import time

import httpx
import pytest

from app.config import get_settings
import re

from app.pipeline import difficulty, knowledge, llm
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


def _llm_on(monkeypatch, handler):  # noqa: ANN001
    s = get_settings()
    for k, v in {"llm_base_url": "https://llm.test/v1", "llm_api_key": "k", "llm_model": "m"}.items():
        monkeypatch.setattr(s, k, v)

    def wrapped(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        out = handler(body["messages"][0]["content"], body["messages"][1]["content"])
        content = json.dumps(out, ensure_ascii=False)
        sse = f'data: {json.dumps({"choices": [{"delta": {"content": content}}]})}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(wrapped))


def _catalog_id(system: str, suffix: str) -> str:
    """从清单「k12：… / 交集」中找到以 suffix 结尾的编号。"""
    return next(line.split("：")[0] for line in system.splitlines() if line.startswith("k") and line.endswith(suffix))


def _nos(user: str) -> list[int]:
    return [int(x) for x in re.findall(r"第 (\d+) 题", user)]


def _wait_knowledge(client, job_id):  # noqa: ANN001
    deadline = time.time() + 10
    while time.time() < deadline:
        st = next(x for x in client.get(f"/api/parse-jobs/{job_id}").json()["stages"] if x["stage"] == "knowledge")
        if st["status"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("知识点标注超时")


def test_tag_with_tree_full_list(client, paper, monkeypatch):  # noqa: F811
    calls = []

    def handler(system: str, user: str) -> dict:
        calls.append(system)
        assert "k0：必修第一册 / 第一章 集合与常用逻辑用语 / 1.1 集合的概念 / 集合的含义与表示" in system
        ids = [_catalog_id(system, "/ 交集"), _catalog_id(system, "/ 一元二次不等式的解法"), "k99999"]
        return {"questions": [{"no": n, "ids": ids, "other": ["树外的新知识点"], "difficulty": 0.4} for n in _nos(user)]}

    _llm_on(monkeypatch, handler)
    assert client.post(f"/api/parse-jobs/{paper['id']}/tag-knowledge").status_code == 202
    st = _wait_knowledge(client, paper["id"])
    assert st["status"] == "done" and "高中数学知识体系" in st["note"]
    kps = client.get(f"/api/parse-jobs/{paper['id']}/questions").json()[0]["knowledgePoints"]
    # 不存在的编号被丢弃；清单外的名称保留为树外知识点
    assert [(k["name"], k["inTree"]) for k in kps] == [("交集", True), ("一元二次不等式的解法", True), ("树外的新知识点", False)]
    assert kps[0]["path"] == "必修第一册 / 第一章 集合与常用逻辑用语 / 1.3 集合的基本运算 / 交集"
    assert len(calls) == 1
    r = client.post(f"/api/parse-jobs/{paper['id']}/tag-knowledge")
    assert r.status_code == 400 and "都已标注" in r.json()["message"]


def test_tag_with_large_tree_retrieves_candidates(client, paper, monkeypatch):  # noqa: F811
    monkeypatch.setattr(knowledge, "FULL_LIST_MAX", 5)  # 让示例树走「名称 → 候选 → 选择」
    steps = []

    def handler(system: str, user: str) -> dict:
        if "候选知识点" in system:
            steps.append("choose")
            cid = next(ln.strip().split("：")[0] for ln in user.splitlines() if ln.strip().endswith("/ 交集"))
            return {"questions": [{"no": n, "ids": [cid, "c99999"]} for n in _nos(user)]}
        steps.append("free")
        return {"questions": [{"no": n, "kps": ["交集运算", "一元二次不等式"], "difficulty": 0.5} for n in _nos(user)]}

    _llm_on(monkeypatch, handler)
    client.post(f"/api/parse-jobs/{paper['id']}/tag-knowledge")
    assert _wait_knowledge(client, paper["id"])["status"] == "done"
    assert steps == ["free", "choose"]
    kps = client.get(f"/api/parse-jobs/{paper['id']}/questions").json()[0]["knowledgePoints"]
    assert [(k["name"], k["inTree"]) for k in kps] == [("交集", True)]


def test_tag_without_tree_uses_free_names(client, paper, monkeypatch):  # noqa: F811
    client.put(f"/api/parse-jobs/{paper['id']}/meta", json=dict(paper["meta"], subject="信息技术"))
    _llm_on(monkeypatch, lambda system, user: {"questions": [
        {"no": n, "kps": ["1．算法与程序设计", "算法与程序设计"]} for n in _nos(user)]})
    client.post(f"/api/parse-jobs/{paper['id']}/tag-knowledge")
    assert _wait_knowledge(client, paper["id"])["status"] == "done"
    kps = client.get(f"/api/parse-jobs/{paper['id']}/questions").json()[3]["knowledgePoints"]
    assert kps == [{"id": kp_id("信息技术", "算法与程序设计"), "name": "算法与程序设计", "path": None, "inTree": False}]


def test_pipeline_applies_ai_difficulty_and_calibration(client, monkeypatch):  # noqa: F811
    def handler(system: str, user: str) -> dict:
        if "知识点清单" in system:
            return {"questions": [{"no": n, "ids": [_catalog_id(system, "/ 交集")], "difficulty": 0.9 if n == 1 else None}
                                  for n in _nos(user)]}
        return {}  # 拆题、分类返回无效结果：回退到规则

    _llm_on(monkeypatch, handler)
    difficulty.set_calibration(0.5, 0.1, source="测试")
    try:
        key = upload(client, "难度.pdf", make_exam_pdf())
        job = client.post("/api/parse-jobs", json={"fileKeys": [key], "fileNames": ["难度.pdf"], "options": {"dedupe": False}}).json()
        done = wait_done(client, job["id"])
    finally:
        difficulty.set_calibration(None)
    st = next(x for x in done["stages"] if x["stage"] == "difficulty")
    assert st["note"] == "AI 评估 1 题"
    qs = client.get(f"/api/parse-jobs/{job['id']}/questions").json()
    # 第 1 题基线为单选题第一题 0.12
    assert qs[0]["difficultySource"] == "ai"
    assert qs[0]["coef"] == pytest.approx(difficulty.combine(0.9, 0.12, {"a": 0.5, "b": 0.1}))
    assert qs[1]["difficultySource"] == "baseline"
    # 老师调整难度后标记为人工
    r = client.patch(f"/api/draft-questions/{qs[1]['id']}", json={"coef": 0.77}).json()
    assert r["difficultySource"] == "manual"


def test_combine_and_calibration():
    assert difficulty.combine(None, 0.3) == 0.3
    assert difficulty.combine(0.8, 0.2) == pytest.approx(0.62)
    assert difficulty.combine(1.0, 1.0, {"a": 2, "b": 0}) == 0.98  # 截断
    difficulty.set_calibration(1.1, -0.05, source="测试")
    assert difficulty.get_calibration() == {"a": 1.1, "b": -0.05, "source": "测试"}
    difficulty.set_calibration(None)
    assert difficulty.get_calibration() is None


def test_patch_only_marks_real_changes(client, paper):  # noqa: F811
    q = client.get(f"/api/parse-jobs/{paper['id']}/questions").json()[0]
    assert q["answerSource"] == "paper"
    # 编辑弹窗带上全部字段，但答案未变：来源保持原卷
    r = client.patch(f"/api/draft-questions/{q['id']}", json={
        "stem": q["stem"] + "。", "answer": q["answer"], "analysis": q["analysis"],
        "knowledgePoints": [{"id": "kp_随便", "name": "集合的基本运算"}, {"id": "x", "name": " 集合的基本运算 "}],
    }).json()
    assert r["answerSource"] == "paper" and r["confidence"] == 1.0
    # 名称与内置知识树节点「1.3 集合的基本运算」一致（忽略编号），归入该节点
    [kp] = r["knowledgePoints"]
    assert kp["inTree"] and kp["name"] == "1.3 集合的基本运算" and kp["path"].endswith("1.3 集合的基本运算")
    r = client.patch(f"/api/draft-questions/{q['id']}", json={"knowledgePoints": [{"id": "", "name": "自创知识点"}]}).json()
    assert r["knowledgePoints"] == [{"id": kp_id("数学", "自创知识点"), "name": "自创知识点", "path": None, "inTree": False}]
    r = client.patch(f"/api/draft-questions/{q['id']}", json={"answer": "B"}).json()
    assert r["answerSource"] == "manual"
