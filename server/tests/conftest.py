import os
import tempfile

# 必须在导入 app 之前设置：测试使用临时目录，且不读取 server/.env 中的真实 Key
_tmp = tempfile.mkdtemp(prefix="fg-quiz-test-")
os.environ.update({
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
