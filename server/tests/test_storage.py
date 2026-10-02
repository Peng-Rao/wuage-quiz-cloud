"""阿里云 OSS、腾讯云 COS 存储与看图模型：读写以内存替身模拟，签名使用真实 SDK（离线计算，不访问网络）。"""
import json
from urllib.parse import urlparse

import alibabacloud_oss_v2 as oss
import httpx
import pytest

from app import storage
from app.config import get_settings
from app.db import DraftQuestion, SessionLocal
from app.pipeline import llm
from app.pipeline.answer import _normalize, _question_payload
from app.pipeline.vision import image_inputs
from app.storage import CosStore, OssStore

from .test_answer import llm_on, wait_answers  # noqa: F401
from .test_api import client, parsed, upload, wait_done  # noqa: F401


class FakeOss:
    """只实现 OssStore 用到的方法；presign 交给真实客户端，验证签名参数。"""

    def __init__(self):
        cfg = oss.config.load_default()
        cfg.credentials_provider = oss.credentials.StaticCredentialsProvider("test-ak", "test-sk")
        cfg.region = "cn-hangzhou"
        self.real = oss.Client(cfg)
        self.objects: dict[str, bytes] = {}

    def presign(self, request, **kw):  # noqa: ANN001, ANN003
        return self.real.presign(request, **kw)

    def put_object(self, r):  # noqa: ANN001
        self.objects[r.key] = r.body

    def get_object(self, r):  # noqa: ANN001
        import io
        return type("R", (), {"body": io.BytesIO(self.objects[r.key])})()

    def is_object_exist(self, bucket, key):  # noqa: ANN001
        return key in self.objects

    def head_object(self, r):  # noqa: ANN001
        return type("R", (), {"content_length": len(self.objects[r.key])})()


@pytest.fixture
def oss_store(monkeypatch):
    s = get_settings()
    for k, v in {"oss_bucket": "fg-quiz-test", "oss_region": "cn-hangzhou", "oss_access_key_id": "test-ak",
                 "oss_access_key_secret": "test-sk", "oss_prefix": "/env/test/"}.items():
        monkeypatch.setattr(s, k, v)
    store = OssStore(s, client=FakeOss())
    monkeypatch.setattr(storage, "_store", store)
    return store


def test_oss_store_roundtrip_and_prefix(oss_store):
    oss_store.put("jobs/j1/blocks/b1.jpg", b"img")
    assert oss_store.client.objects == {"env/test/jobs/j1/blocks/b1.jpg": b"img"}
    assert oss_store.get("jobs/j1/blocks/b1.jpg") == b"img"
    assert oss_store.exists("jobs/j1/blocks/b1.jpg") and not oss_store.exists("jobs/j1/x.jpg")
    assert not oss_store.exists("jobs/../secret")
    assert oss_store.size("jobs/j1/blocks/b1.jpg") == 3
    url = urlparse(oss_store.signed_url("jobs/j1/blocks/b1.jpg", 600))
    assert url.hostname == "fg-quiz-test.oss-cn-hangzhou.aliyuncs.com"
    assert url.path == "/env/test/jobs/j1/blocks/b1.jpg" and "x-oss-signature=" in url.query


def test_oss_upload_ticket_signs_content_type_and_forbids_overwrite(oss_store):
    url, headers = oss_store.upload_ticket("uploads/a.pdf", "application/pdf")
    assert urlparse(url).path == "/env/test/uploads/a.pdf"
    assert headers["Content-Type"] == "application/pdf"
    assert headers["x-oss-forbid-overwrite"].lower() == "true"


def test_oss_requires_config():
    s = get_settings().model_copy(update={"oss_bucket": ""})
    with pytest.raises(RuntimeError, match="OSS_BUCKET"):
        OssStore(s)


def test_api_with_oss(client, parsed, oss_store):  # noqa: F811
    r = client.post("/api/uploads", json={"fileName": "卷.pdf", "fileSize": 10, "contentType": "application/pdf"})
    ticket = r.json()
    assert ticket["uploadUrl"].startswith("https://fg-quiz-test.oss-cn-hangzhou.aliyuncs.com/env/test/uploads/")
    assert ticket["uploadHeaders"]["Content-Type"] == "application/pdf"
    # 直传 OSS 时不接受经本服务上传
    assert client.put(f"/api/files/{ticket['fileKey']}", content=b"x").status_code == 404

    # 超过大小限制的文件在创建任务时拒绝
    oss_store.client.objects["env/test/" + ticket["fileKey"]] = b"x" * (get_settings().max_file_mb * 1024 * 1024 + 1)
    r = client.post("/api/parse-jobs", json={"fileKeys": [ticket["fileKey"]], "fileNames": ["卷.pdf"], "options": {}})
    assert r.status_code == 400 and "MB" in r.json()["message"]

    # 读取：校验权限后跳转到签名地址
    key = f"jobs/{parsed['id']}/pages/1.png"
    r = client.get(f"/api/files/{key}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["cache-control"] == "no-store"
    assert urlparse(r.headers["location"]).path == f"/env/test/{key}"
    assert client.get("/api/files/uploads/x.pdf", follow_redirects=False).status_code == 403


# ---------------- 腾讯云 COS ----------------

class FakeCos:
    """只实现 CosStore 用到的方法；get_presigned_url 交给真实客户端，验证签名参数。"""

    def __init__(self):
        from qcloud_cos import CosConfig, CosS3Client
        self.real = CosS3Client(CosConfig(Region="ap-guangzhou", SecretId="test-id", SecretKey="test-key", Scheme="https"))
        self.objects: dict[str, bytes] = {}

    def get_presigned_url(self, **kw):  # noqa: ANN003
        return self.real.get_presigned_url(**kw)

    def put_object(self, Bucket, Key, Body):  # noqa: ANN001, N803
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):  # noqa: ANN001, N803
        import io
        return {"Body": type("B", (), {"get_raw_stream": lambda _: io.BytesIO(self.objects[Key])})()}

    def object_exists(self, Bucket, Key):  # noqa: ANN001, N803
        return Key in self.objects

    def head_object(self, Bucket, Key):  # noqa: ANN001, N803
        return {"Content-Length": str(len(self.objects[Key]))}


@pytest.fixture
def cos_store(monkeypatch):
    s = get_settings()
    for k, v in {"cos_bucket": "fg-quiz-1250000000", "cos_region": "ap-guangzhou", "cos_secret_id": "test-id",
                 "cos_secret_key": "test-key", "cos_prefix": "/env/test/"}.items():
        monkeypatch.setattr(s, k, v)
    store = CosStore(s, client=FakeCos())
    monkeypatch.setattr(storage, "_store", store)
    return store


class Flaky:
    """前 n 次调用抛出网络错误，之后交给真实替身。"""

    def __init__(self, inner: FakeCos, failures: int, error: Exception):
        self.inner, self.left, self.error, self.calls = inner, failures, error, 0

    def __getattr__(self, name):  # noqa: ANN001, ANN204
        target = getattr(self.inner, name)

        def call(*a, **kw):  # noqa: ANN002, ANN003, ANN202
            self.calls += 1
            if self.left > 0 and name != "get_presigned_url":
                self.left -= 1
                raise self.error
            return target(*a, **kw)

        return call


def test_cos_retries_network_errors(cos_store, monkeypatch):
    from qcloud_cos.cos_exception import CosClientError, CosServiceError

    monkeypatch.setattr(storage.time, "sleep", lambda _: None)
    cos_store.put("jobs/j/a.png", b"png")
    # 读取超时两次后成功（默认最多 3 次）
    flaky = Flaky(cos_store.client, 2, CosClientError("Read timed out. (read timeout=30)"))
    monkeypatch.setattr(cos_store, "client", flaky)
    assert cos_store.get("jobs/j/a.png") == b"png" and flaky.calls == 3
    flaky.left = 2
    cos_store.put("jobs/j/b.png", b"b")
    assert cos_store.size("jobs/j/b.png") == 1
    # 一直失败：重试用尽后抛出
    flaky.left = 99
    with pytest.raises(CosClientError):
        cos_store.get("jobs/j/a.png")
    # COS 返回的业务错误（如 404）不重试
    flaky.left, flaky.calls = 5, 0
    flaky.error = CosServiceError("GET", "<Error><Code>NoSuchKey</Code></Error>", 404)
    with pytest.raises(CosServiceError):
        cos_store.get("jobs/j/missing.png")
    assert flaky.calls == 1


def test_cos_store_roundtrip_and_prefix(cos_store):
    cos_store.put("jobs/j1/blocks/b1.jpg", b"img")
    assert cos_store.client.objects == {"env/test/jobs/j1/blocks/b1.jpg": b"img"}
    assert cos_store.get("jobs/j1/blocks/b1.jpg") == b"img"
    assert cos_store.exists("jobs/j1/blocks/b1.jpg") and not cos_store.exists("jobs/j1/x.jpg")
    assert not cos_store.exists("jobs/../secret")
    assert cos_store.size("jobs/j1/blocks/b1.jpg") == 3
    url = urlparse(cos_store.signed_url("jobs/j1/blocks/b1.jpg", 600))
    assert url.hostname == "fg-quiz-1250000000.cos.ap-guangzhou.myqcloud.com"
    assert url.path == "/env/test/jobs/j1/blocks/b1.jpg" and "q-signature=" in url.query


def test_cos_upload_ticket_signs_content_type_and_forbids_overwrite(cos_store):
    url, headers = cos_store.upload_ticket("uploads/a.pdf", "application/pdf")
    assert urlparse(url).path == "/env/test/uploads/a.pdf"
    assert headers == {"Content-Type": "application/pdf", "x-cos-forbid-overwrite": "true"}
    assert "q-header-list=content-type%3Bhost%3Bx-cos-forbid-overwrite" in url


def test_cos_requires_config():
    s = get_settings().model_copy(update={"cos_bucket": ""})
    with pytest.raises(RuntimeError, match="COS_BUCKET"):
        CosStore(s)


def test_api_with_cos(client, parsed, cos_store):  # noqa: F811
    r = client.post("/api/uploads", json={"fileName": "卷.pdf", "fileSize": 10, "contentType": "application/pdf"})
    ticket = r.json()
    assert ticket["uploadUrl"].startswith("https://fg-quiz-1250000000.cos.ap-guangzhou.myqcloud.com/env/test/uploads/")
    assert ticket["uploadHeaders"]["x-cos-forbid-overwrite"] == "true"
    assert client.put(f"/api/files/{ticket['fileKey']}", content=b"x").status_code == 404

    key = f"jobs/{parsed['id']}/pages/1.png"
    r = client.get(f"/api/files/{key}", follow_redirects=False)
    assert r.status_code == 302 and urlparse(r.headers["location"]).path == f"/env/test/{key}"


# ---------------- 看图模型 ----------------

def test_image_inputs_modes(oss_store, monkeypatch):
    s = get_settings()
    oss_store.put("jobs/j/blocks/a.jpg", b"\xff\xd8jpeg")
    monkeypatch.setattr(s, "vision_image_mode", "auto")
    assert image_inputs(["jobs/j/blocks/a.jpg"], s)[0].startswith("https://fg-quiz-test.")
    monkeypatch.setattr(s, "vision_image_mode", "base64")
    assert image_inputs(["jobs/j/blocks/a.jpg"], s) == ["data:image/jpeg;base64,/9hqcGVn"]
    monkeypatch.setattr(s, "vision_max_images", 1)
    assert len(image_inputs(["jobs/j/blocks/a.jpg"] * 3, s)) == 1


def test_image_inputs_local_store_uses_base64(monkeypatch):
    s = get_settings()
    storage.get_store().put("jobs/j/blocks/b.png", b"png")
    assert image_inputs(["jobs/j/blocks/b.png"], s) == ["data:image/png;base64,cG5n"]
    monkeypatch.setattr(s, "vision_image_mode", "url")
    with pytest.raises(ValueError, match="OSS"):
        image_inputs(["jobs/j/blocks/b.png"], s)


async def test_chat_json_with_images_uses_vision_model(monkeypatch):
    s = get_settings()
    for k, v in {"llm_base_url": "https://llm.test/v1", "llm_api_key": "k", "llm_model": "text-model",
                 "llm_extra_body": {"enable_thinking": False}, "vision_model": "vl-model",
                 "vision_base_url": "https://vl.test/v1"}.items():
        monkeypatch.setattr(s, k, v)
    seen: list[tuple[str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((str(req.url), json.loads(req.content)))
        return httpx.Response(200, json={"choices": [{"message": {"content": '说明：{"answer": "B"}'}}]})

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handler))
    assert await llm.chat_json("sys", "题目", s, images=["https://img/1.jpg", "data:image/png;base64,AA=="]) == {"answer": "B"}
    url, body = seen[0]
    assert url == "https://vl.test/v1/chat/completions" and body["model"] == "vl-model"
    assert body["enable_thinking"] is False and "response_format" not in body
    assert body["messages"][1]["content"] == [
        {"type": "text", "text": "题目"},
        {"type": "image_url", "image_url": {"url": "https://img/1.jpg"}},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]
    # 不带图片时仍用文字模型
    await llm.chat_json("sys", "题目", s)
    assert seen[1][1]["model"] == "text-model" and seen[1][1]["response_format"] == {"type": "json_object"}

    monkeypatch.setattr(s, "vision_model", "")
    with pytest.raises(llm.LLMError, match="看图模型"):
        await llm.chat_json("sys", "题目", s, images=["https://img/1.jpg"])


def test_answer_notes_with_images():
    q = DraftQuestion(type="解答题", stem="如图", options=[], images=["a.png", "b.png"])
    assert "无法看到" in _question_payload(q) and "按顺序附在后面" in _question_payload(q, 2)
    assert _normalize(q, {"answer": "x"})[2] == "题目含图，AI 未看到图片，答案可能不准确"
    assert _normalize(q, {"answer": "x"}, saw_images=True)[2] is None


def test_generate_answer_sends_images(client, parsed, llm_on, monkeypatch):  # noqa: F811
    s = get_settings()
    monkeypatch.setattr(s, "vision_model", "vl-model")
    bodies: list[dict] = []
    inner = llm.transport

    async def spy(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        bodies.append(json.loads(req.content))
        if isinstance(body["messages"][1]["content"], list):  # 看图请求：取出文字部分交给原模拟
            body["messages"][1]["content"] = body["messages"][1]["content"][0]["text"]
            req = httpx.Request(req.method, req.url, headers=req.headers, json=body)
        return await inner.handle_async_request(req)

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(spy))
    job_id = parsed["id"]
    qid = client.get(f"/api/parse-jobs/{job_id}/questions").json()[2]["id"]
    key = f"jobs/{job_id}/blocks/test-fig.png"
    storage.get_store().put(key, b"png")
    with SessionLocal() as db:
        db.get(DraftQuestion, qid).images = [key]
        db.commit()
    r = client.post(f"/api/parse-jobs/{job_id}/generate-answers", json={"questionIds": [qid], "overwrite": True})
    assert r.status_code == 202
    assert wait_answers(client, job_id)["failed"] == 0
    content = bodies[-1]["messages"][1]["content"]
    assert bodies[-1]["model"] == "vl-model" and content[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}}
    assert "按顺序附在后面" in content[0]["text"]
    q = next(x for x in client.get(f"/api/parse-jobs/{job_id}/questions").json() if x["id"] == qid)
    assert q["answerSource"] == "ai" and q["answerNote"] is None
