import os
import tempfile

# 必须在导入 app 之前设置：测试使用临时目录，且不读取 server/.env 中的真实 Key
_tmp = tempfile.mkdtemp(prefix="fg-quiz-test-")
# 模拟构建好的前端，测试页面托管
_web = os.path.join(_tmp, "web")
os.makedirs(os.path.join(_web, "assets"))
with open(os.path.join(_web, "index.html"), "w") as f:
    f.write("<!doctype html><title>index</title>")
with open(os.path.join(_web, "assets", "app-abc123.js"), "w") as f:
    f.write("console.log(1)")
os.environ.update({
    "STATIC_DIR": _web,
    "DATA_DIR": _tmp,
    "DATABASE_URL": "",
    "MINERU_TOKEN": "",
    "LLM_BASE_URL": "",
    "LLM_API_KEY": "",
    "LLM_MODEL": "",
    "PARSER_CHAIN": '["mineru_cloud","lite"]',
    "LLM_PRICES": "{}",
})

import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _db():
    # 用量记录等会直接写库，所有测试共用已建好表的临时数据库
    from app.db import init_db
    init_db()
    from app.auth import hash_password
    from app.db import SessionLocal, User
    with SessionLocal() as s:
        s.add(User(id="test-admin", username="test-admin", display_name="测试管理员", password_hash=hash_password("test-password-123"), role="admin", subjects=[], active=True))
        s.commit()
