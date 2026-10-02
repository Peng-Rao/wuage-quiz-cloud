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
    # 文件存储：local 为 data_dir/storage；oss 为阿里云 OSS，cos 为腾讯云 COS（上传文件、页面图、题目配图都存对象存储）
    storage_backend: Literal["local", "oss", "cos"] = "local"
    # 对象存储（OSS / COS）通用：浏览器直传签名地址、查看配图时重定向到的签名地址的有效期（秒）
    storage_upload_expires: int = 1800
    storage_url_expires: int = 600
    oss_bucket: str = ""
    oss_region: str = ""  # 如 cn-hangzhou
    # 服务端读写用的 Endpoint，留空按地域使用公网地址；与 OSS 同地域的 ECS 可填内网地址，如 oss-cn-hangzhou-internal.aliyuncs.com
    oss_endpoint: str = ""
    # 签名地址（浏览器直传、查看配图、大模型读图）用的公网 Endpoint；OSS_ENDPOINT 为内网地址时必填
    oss_public_endpoint: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    # 对象名前缀，多套环境共用一个 Bucket 时区分，如 fg-quiz/prod
    oss_prefix: str = ""
    # 腾讯云 COS。Bucket 名称带 APPID，如 fg-quiz-1250000000；默认域名在同地域腾讯云内网会自动解析为内网地址
    cos_bucket: str = ""
    cos_region: str = ""  # 如 ap-guangzhou
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    # 对象名前缀，多套环境共用一个 Bucket 时区分，如 fg-quiz/prod
    cos_prefix: str = ""
    # 构建好的前端目录（npm run build:api 的 dist）；设置后由本服务直接托管页面，Docker 镜像中为 /app/web
    static_dir: Path | None = None
    # PostgreSQL；默认连接 docker compose 中的数据库（本机 5433 端口）
    database_url: str = "postgresql+psycopg://fg_quiz:fg-quiz-local@127.0.0.1:5433/fg_quiz?sslmode=disable"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]
    max_file_mb: int = 50
    session_hours: int = 12
    # HTTPS 部署时设为 true；本地 HTTP 开发为 false。
    cookie_secure: bool = False

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
    # 看图模型（可选）：题目含配图时改用该模型并附上图片，如通义千问 qwen3-vl-plus；留空则大模型看不到图片。
    # 地址与 Key 留空时沿用 LLM_BASE_URL / LLM_API_KEY；附加参数留空时沿用 LLM_EXTRA_BODY
    vision_model: str = ""
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_extra_body: dict[str, Any] | None = None
    # 图片传给模型的方式：url 为对象存储签名地址（模型服务自行下载，请求体小），base64 为内嵌图片数据；
    # auto 在使用 OSS / COS 时用 url，否则用 base64（本地存储的地址模型服务访问不到）
    vision_image_mode: Literal["auto", "url", "base64"] = "auto"
    # 每道题最多附带的图片数，控制费用
    vision_max_images: int = 6
    # 相似题语义检索（可选）：OpenAI 兼容 /embeddings；地址与 Key 留空时沿用 LLM_BASE_URL / LLM_API_KEY
    # 如通义千问：EMBEDDING_MODEL=text-embedding-v4
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_batch: int = 10

    # 每个进程同时执行的任务数（解析、生成答案等；批量上传时其余排队）
    worker_concurrency: int = 2
    # 任务队列：配置 REDIS_URL 后由独立 Worker 进程（python -m app.worker）执行任务，可运行多个；
    # 未配置时在网页服务进程内执行。RUN_WORKER=true 时网页服务进程也执行任务（单容器部署）
    redis_url: str = ""
    redis_prefix: str = "fg-quiz"
    run_worker: bool = False

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
        return self.database_url

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    @property
    def vision_enabled(self) -> bool:
        return bool(self.vision_model and (self.vision_base_url or self.llm_base_url)
                    and (self.vision_api_key or self.llm_api_key))


@lru_cache
def get_settings() -> Settings:
    return Settings()
