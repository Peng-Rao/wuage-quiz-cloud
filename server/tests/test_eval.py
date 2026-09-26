import time

import pytest

from app.evaluation import fit_calibration, match
from app.pipeline import difficulty

from .fixtures import make_exam_pdf
from .test_api import client, upload, wait_done  # noqa: F401


def test_match_pairs_by_stem_similarity():
    gold = [{"stem": "已知集合 A = {1, 2}，求 A 的子集个数"}, {"stem": "函数 f(x) = x² 的单调区间"}, {"stem": "完全不同的题目内容"}]
    pred = [{"stem": "函数 f(x)=x² 的单调区间"}, {"stem": "已知集合A={1,2}，求A的子集个数"}]
    assert [(g, p) for g, p, _ in match(gold, pred)] == [(0, 1), (1, 0)]


def test_fit_calibration():
    assert fit_calibration([(0.3, 0.4)] * 4) is None  # 样本不足
    pairs = [(x, round(0.8 * x + 0.1, 3)) for x in (0.1, 0.3, 0.5, 0.7, 0.9)]
    cal = fit_calibration(pairs)
    assert cal["a"] == pytest.approx(0.8) and cal["b"] == pytest.approx(0.1) and cal["maeAfter"] < cal["maeBefore"]


def wait_run(client, run_id, timeout=20):  # noqa: ANN001, F811
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/eval-runs/{run_id}").json()
        if r["status"] in ("done", "failed"):
            return r
        time.sleep(0.1)
    raise AssertionError("评测超时")


def test_eval_flow(client):  # noqa: F811
    key = upload(client, "评测卷.pdf", make_exam_pdf())
    job = wait_done(client, client.post("/api/parse-jobs", json={"fileKeys": [key], "fileNames": ["评测卷.pdf"],
                                                                 "options": {"dedupe": False}}).json()["id"])
    qs = client.get(f"/api/parse-jobs/{job['id']}/questions").json()
    # 老师核对：改一道题型、标注知识点、调整 6 道题难度
    client.patch(f"/api/draft-questions/{qs[5]['id']}", json={"type": "解答题"})
    client.patch(f"/api/draft-questions/{qs[0]['id']}", json={"knowledgePoints": [{"id": "", "name": "交集"}]})
    for i, q in enumerate(qs[:6]):
        client.patch(f"/api/draft-questions/{q['id']}", json={"coef": round(0.8 * q["coef"] + 0.1 + 0.001 * i, 3)})

    r = client.post("/api/eval-runs", json={})
    assert r.status_code == 400 and "还没有评测样本" in r.json()["message"]
    sample = client.post(f"/api/parse-jobs/{job['id']}/eval-sample").json()
    assert sample["questionCount"] == 9
    assert client.get(f"/api/parse-jobs/{job['id']}").json()["evalSampleId"] == sample["id"]
    assert client.post(f"/api/parse-jobs/{job['id']}/eval-sample").json()["id"] == sample["id"]  # 覆盖而非新建

    before = client.get("/api/parse-jobs").json()["total"]
    run = client.post("/api/eval-runs", json={"sampleIds": [sample["id"]]}).json()
    assert run["status"] == "queued" and run["config"]["parserChain"]
    done = wait_run(client, run["id"])
    assert done["status"] == "done", done
    m = done["metrics"]
    assert m["splitPrecision"] == 1 and m["splitRecall"] == 1
    assert m["typeAccuracy"] == pytest.approx(8 / 9, abs=1e-3)  # 第 6 题题型被老师改过
    assert m["answerAccuracy"] == 1 and m["optionAccuracy"] == 1 and m["metaAccuracy"] == 1
    assert m["knowledgeTop3"] == 0  # 未配置大模型：解析结果没有知识点
    assert m["difficultyMae"] > 0 and m["calibration"]["n"] == 6
    detail = done["details"][0]
    assert any("题型" in i for q in detail["questions"] for i in q["issues"])
    # 评测任务不出现在任务列表中
    assert client.get("/api/parse-jobs").json()["total"] == before

    cal = client.post(f"/api/eval-runs/{run['id']}/apply-calibration").json()
    assert cal["a"] == pytest.approx(0.8, abs=0.05) and client.get("/api/difficulty-calibration").json()["a"] == cal["a"]
    assert client.delete("/api/difficulty-calibration").status_code == 204
    assert difficulty.get_calibration() is None
    assert client.delete(f"/api/eval-samples/{sample['id']}").status_code == 204
