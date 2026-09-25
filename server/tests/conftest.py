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
})
