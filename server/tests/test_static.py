"""生产部署时由后端托管前端页面（Docker 镜像中 STATIC_DIR=/app/web）。"""

from .test_api import client  # noqa: F401


def test_spa_fallback_and_assets(client):  # noqa: F811
    for path in ("/", "/upload", "/upload/eval?job=abc"):
        r = client.get(path)
        assert r.status_code == 200 and "<title>index</title>" in r.text
        assert r.headers["cache-control"] == "no-cache"
    r = client.get("/assets/app-abc123.js")
    assert r.text == "console.log(1)" and "immutable" in r.headers["cache-control"]
    assert client.head("/upload").status_code == 200


def test_api_paths_are_not_swallowed(client):  # noqa: F811
    r = client.get("/api/not-exists")
    assert r.status_code == 404 and r.json() == {"message": "接口不存在"}
    assert client.get("/api/health").json()["ok"] is True


def test_no_path_traversal(client):  # noqa: F811
    r = client.get("/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code == 200 and "<title>index</title>" in r.text  # 回退到首页，不读取目录外文件
