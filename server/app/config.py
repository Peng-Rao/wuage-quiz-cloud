from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ROOT = Path(__file__).resolve().parent.parent

ParserName = Literal["mineru_cloud", "mineru_local", "lite"]


class Settings(BaseSettings):
    """所有配置均可通过环境变量或 server/.env 覆盖，变量名与字段名一致（大小写不敏感）。"""

    model_config = SettingsConfigDict(env_file=SERVER_ROOT / ".env", extra="ignore")

    data_dir: Path = SERVER_ROOT / "data"
    database_url: str = ""  # 留空时使用 data_dir/app.db（SQLite）
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]
    max_file_mb: int = 50

    # 解析引擎：按顺序尝试，前一个不可用或失败时降级到下一个
    parser_chain: list[ParserName] = ["mineru_cloud", "lite"]

    # MinerU 云端 https://mineru.net/apiManage
    mineru_token: str = ""
    mineru_base_url: str = "https://mineru.net"
    mineru_model_version: Literal["pipeline", "vlm"] = "vlm"
    mineru_poll_interval: float = 3.0
    mineru_timeout: float = 900.0

    # 大模型（OpenAI 兼容 /chat/completions 接口：DeepSeek、通义千问、Kimi 等）
    llm_base_url: str = ""  # 如 https://api.deepseek.com/v1
    llm_api_key: str = ""
    llm_model: str = ""  # 如 deepseek-chat、qwen-plus
    llm_timeout: float = 300.0
    # 附加到请求体的厂商参数（JSON），如通义千问关闭思考：{"enable_thinking": false}
    llm_extra_body: dict[str, Any] = {}
    # 相似题语义检索（可选）：OpenAI 兼容 /embeddings；地址与 Key 留空时沿用 LLM_BASE_URL / LLM_API_KEY
    # 如通义千问：EMBEDDING_MODEL=text-embedding-v4
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_batch: int = 10

    # 同时解析的试卷数（批量上传时其余排队）
    worker_concurrency: int = 2

    # AI 生成答案时同时进行的请求数
    answer_concurrency: int = 3
    #  单次拆题请求最多发送的版面单元数，超过则只用规则结果
    llm_max_units: int = 800

    # 成本估算单价（留空则只统计用量，不估算费用）。按模型配置，单位：元 / 百万 tokens
    # 例：LLM_PRICES={"qwen-plus": {"input": 0.8, "output": 2, "cached_input": 0.16}}
    llm_prices: dict[str, dict[str, float]] = {}
    # MinerU 单价：元 / 页
    mineru_price_per_page: float | None = None
    currency: str = "¥"

    # 页面渲染分辨率（查看原图）
    page_dpi: int = 110

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'app.db'}"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
