import os
import tempfile

from sqlalchemy import create_engine, make_url, text

# 测试库：默认为 docker compose 中 PostgreSQL 上的 fg_quiz_test（不存在时自动创建），可用 TEST_DATABASE_URL 指定。
# 测试会清空重建库中的表：库名须以 _test 结尾，避免误连开发 / 生产库
TEST_DB = make_url(os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://fg_quiz:fg-quiz-local@127.0.0.1:5433/fg_quiz_test?sslmode=disable"))
if not (TEST_DB.database or "").endswith("_test"):
    raise SystemExit(f"测试库名须以 _test 结尾（当前：{TEST_DB.database}），测试会清空其中的表")


def _admin():  # noqa: ANN202
    """连接同一服务器的 postgres 库，用于建库、删库。"""
    return create_engine(TEST_DB.set(database="postgres"), isolation_level="AUTOCOMMIT")


try:
    with _admin().connect() as _c:
        if not _c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB.database}).scalar():
            _c.execute(text(f'CREATE DATABASE "{TEST_DB.database}"'))
except Exception as _e:  # noqa: BLE001
    raise SystemExit(f"无法连接测试用的 PostgreSQL（{TEST_DB.render_as_string(hide_password=True)}）："
                     f"先 docker compose up -d db，或设置 TEST_DATABASE_URL。{type(_e).__name__}: {_e}") from None

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
    "DATABASE_URL": TEST_DB.render_as_string(hide_password=False),
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
    from app.db import Base, engine, init_db
    Base.metadata.drop_all(engine)
    init_db()
    from app.auth import hash_password
    from app.db import SessionLocal, User
    with SessionLocal() as s:
        s.add(User(id="test-admin", username="test-admin", display_name="测试管理员", password_hash=hash_password("test-password-123"), role="admin", subjects=[], active=True))
        s.commit()


@pytest.fixture
def scratch_engine():
    """临时新建的空库（如模拟升级前的旧表结构），用完删除。"""
    name = f"{TEST_DB.database}_scratch"
    admin = _admin()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(TEST_DB.set(database=name), connect_args={"options": "-c timezone=UTC"})
    yield eng
    eng.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()
