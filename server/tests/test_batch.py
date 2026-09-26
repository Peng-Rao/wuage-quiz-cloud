import time

from app.worker import worker

from .fixtures import make_exam_pdf
from .test_api import client, upload, wait_done  # noqa: F401


def _item(client, name: str) -> dict:  # noqa: ANN001, F811
    return {"fileKeys": [upload(client, name, make_exam_pdf())], "fileNames": [name]}


def test_batch_parses_all_papers(client):  # noqa: F811
    r = client.post("/api/parse-batches", json={"items": [_item(client, "批量-1.pdf"), _item(client, "批量-2.pdf")],
                                                "options": {"dedupe": False}})
    assert r.status_code == 200, r.text
    batch = r.json()
    assert batch["total"] == 2 and len(batch["jobs"]) == 2
    for j in batch["jobs"]:
        assert j["batchId"] == batch["id"]
        assert wait_done(client, j["id"])["status"] == "done"
    done = client.get(f"/api/parse-batches/{batch['id']}").json()
    assert done["counts"] == {"done": 2} and all(j["questionCount"] == 9 for j in done["jobs"])

    page = client.get(f"/api/parse-jobs?batchId={batch['id']}").json()
    assert page["total"] == 2 and page["active"] == 0
    assert [i["fileName"] for i in page["items"]] == ["批量-2.pdf", "批量-1.pdf"]  # 最新的在前
    assert client.get("/api/parse-jobs?status=failed,cancelled&limit=1").status_code == 200


def test_batch_rejects_invalid_item_atomically(client):  # noqa: F811
    before = client.get("/api/parse-jobs").json()["total"]
    bad = {"fileKeys": [upload(client, "a.pdf", b"%PDF"), upload(client, "b.png", b"PNG")], "fileNames": ["a.pdf", "b.png"]}
    r = client.post("/api/parse-batches", json={"items": [_item(client, "批量-ok.pdf"), bad]})
    assert r.status_code == 400 and "a.pdf" in r.json()["message"]
    assert client.get("/api/parse-jobs").json()["total"] == before
    assert client.get("/api/parse-batches/nope").status_code == 404


def test_cancel_and_retry(client, monkeypatch):  # noqa: F811
    # 暂停入队，让任务停留在排队状态
    queued: list[str] = []
    monkeypatch.setattr(worker, "enqueue", queued.append)
    job = client.post("/api/parse-jobs", json=_item(client, "取消.pdf")).json()
    assert job["status"] == "queued" and queued == [job["id"]]
    assert client.get("/api/parse-jobs?status=queued").json()["active"] >= 1

    assert client.post(f"/api/parse-jobs/{job['id']}/retry").status_code == 400
    r = client.post(f"/api/parse-jobs/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.post(f"/api/parse-jobs/{job['id']}/cancel").status_code == 400

    monkeypatch.undo()
    r = client.post(f"/api/parse-jobs/{job['id']}/retry")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert wait_done(client, job["id"])["status"] == "done"


def test_cancelled_job_is_skipped_by_worker(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(worker, "enqueue", lambda _: None)
    job = client.post("/api/parse-jobs", json=_item(client, "跳过.pdf")).json()
    client.post(f"/api/parse-jobs/{job['id']}/cancel")
    monkeypatch.undo()
    worker.enqueue(job["id"])  # 模拟重启后仍在队列里
    time.sleep(0.5)
    assert client.get(f"/api/parse-jobs/{job['id']}").json()["status"] == "cancelled"
