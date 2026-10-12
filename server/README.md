# 福格云上题库 · 解析服务（P1）

[![CI](https://github.com/Peng-Rao/wuage-quiz-cloud/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Peng-Rao/wuage-quiz-cloud/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-%E2%89%A53.11-3776AB?logo=python&logoColor=white)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

FastAPI 实现的试卷解析后端，接口契约见 [docs/ai-parse-api.md](../docs/ai-parse-api.md)。

## 启动

本地开发与 Docker 部署的完整步骤见 [部署指南](../docs/deployment.md)。简要步骤：

```bash
docker compose up -d db redis   # 在仓库根目录执行；PostgreSQL 本机 5433、Redis 本机 6380
cd server
uv sync
cp .env.example .env            # 填写 MINERU_TOKEN、LLM_*（可先不填）
uv run python -m app.create_admin admin  # 首次创建管理员，交互输入密码
uv run uvicorn app.main:app --port 8000 --reload
```

默认任务在网页服务进程内执行。在 `.env` 中设置 `REDIS_URL=redis://localhost:6380/0` 后改由独立 Worker 执行，
另开终端启动（可启动多个）：`uv run python -m app.worker`。

`GET /api/health` 可查看当前启用了哪些引擎与在线 Worker：`{"mineru": true, "llm": true, "worker": {...}, ...}`。

## 认证和权限

除健康检查、登录、退出外，业务接口均要求登录 Cookie。管理员通过 `/api/users` 管理账号；组长限授权学科，普通用户限本人已分配且审核通过的题目。请求级数据库会话统一过滤题目、任务、知识树和聚合统计；文件接口另校验题目配图权限，普通用户不能读取整页原图。\
- `POST /api/auth/login`：`{username, password}`，返回用户资料并设置 HttpOnly Cookie。
- `GET /api/auth/me`：恢复会话；`POST /api/auth/logout`：撤销会话。
- `GET/POST /api/users`、`PUT /api/users/{id}`：仅管理员，创建/修改时传 `displayName, role, subjects, active, password`；更新可省略密码。
- `GET /api/review-recipients`：管理员/组长可分配的用户。
- `POST /api/bank/review`：管理员/组长提交 `{questionIds, ownerId, approved}`，最多 100 题。
- `PATCH /api/bank/questions/{id}`：仅管理员，直接修改已入库的题（字段同草稿题编辑），同步到原卷草稿题；审核与归属不变。
- `PUT /api/papers/{id}/meta`：仅管理员，修改试卷属性（字段同 `PUT /api/parse-jobs/{id}/meta`，学段与学科不能为空），同步到原卷与本卷已入库的题；改了学科的题撤销审核。返回试卷详情。
- `POST /api/compose`：AI 组卷，仅管理员/组长；提交 `{stage, subject, total, difficulty, messages}`（完整对话），组长只能为授权学科组卷，候选题同样受数据权限过滤。
- 上传创建任务时 `options.subject` 为当前学科，组长必填且必须属于授权学科。上传凭证绑定创建者，不能复用他人的上传文件。
- 历史题目不会自动开放给普通用户，需在试卷详情重新审核并分配。

账号密码和会话均不存明文；登录失败按账号与来源地址限流。修改角色、学科、密码或停用账号将撤销其所有会话。前后端推荐同源，跨源开发须正确配置 `CORS_ORIGINS`。HTTPS 设置 `COOKIE_SECURE=true`。

## 配置

在线状态：`POST /api/auth/heartbeat` 使用当前登录 Cookie 更新心跳，不延长会话有效期；`GET /api/users` 仅向管理员返回 `isOnline` 与 `lastSeenAt`（UTC）。最近 90 秒内有登录或心跳且会话未过期的启用账号视为在线；最后在线时间持久保存，退出或撤销会话后仍可查看。启动时自动为旧用户表、会话表添加可空的 `last_seen_at` 列；旧账号在首次登录或心跳前暂无在线记录。

| 变量 | 说明 |
|---|---|
| `PARSER_CHAIN` | 解析引擎链，默认 `["mineru_cloud","lite"]`：未配置或失败时依次降级 |
| `MINERU_TOKEN` | MinerU 云端 Token（https://mineru.net/apiManage），未填时跳过云端 |
| `MINERU_MODEL_VERSION` | `vlm`（默认，公式更准）或 `pipeline` |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容接口，三项齐全才启用；未配置时只用规则拆题 |
| `LLM_EXTRA_BODY` | 附加到请求体的厂商参数（JSON），如 `{"enable_thinking": false}` |
| `WORKER_CONCURRENCY` | 每个进程同时执行的任务数（解析、生成答案等），其余排队，默认 2 |
| `REDIS_URL` | 任务队列，如 `redis://localhost:6379/0`。配置后网页服务只负责入队，由 `python -m app.worker` 启动的独立进程执行（可运行多个）；未配置时在网页服务进程内执行 |
| `REDIS_PREFIX` | Redis 键前缀，多套环境共用一个 Redis 时区分，默认 `fg-quiz` |
| `RUN_WORKER` | 配置了 Redis 时网页服务进程也执行任务（单容器部署），默认 false |
| `EMBEDDING_MODEL` | 相似题语义检索的向量模型（如 `text-embedding-v4`），留空则只用字面相似度 |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` | 向量接口地址与 Key，留空沿用 `LLM_BASE_URL` / `LLM_API_KEY` |
| `ANSWER_CONCURRENCY` | AI 生成答案时同时进行的请求数，默认 3 |
| `LLM_PRICES` | 成本估算单价，按模型名，元 / 百万 tokens：`{"qwen-plus": {"input": 0.8, "output": 2, "cached_input": 0.16}}` |
| `MINERU_PRICE_PER_PAGE` | MinerU 单价，元 / 页 |
| `CURRENCY` | 金额前缀，默认 `¥` |
| `DATABASE_URL` | PostgreSQL，如 `postgresql+psycopg://user:pass@host:5432/db`；默认连接 docker compose 中的数据库（本机 5433 端口） |
| `SESSION_HOURS` | 会话有效期，默认 12 小时 |
| `COOKIE_SECURE` | HTTPS 部署设为 true，本地 HTTP 开发为 false |
| `CORS_ORIGINS` | 允许跨源访问的前端来源（JSON 数组），默认 `["http://localhost:5173","http://localhost:5174"]`；同源部署无需修改 |
| `MAX_FILE_MB` | 上传文件大小上限，默认 50 |
| `PAGE_DPI` | 页面图渲染分辨率，默认 110 |
| `DATA_DIR` | 本地存储的数据目录，默认 `server/data`（镜像内为 `/data`） |
| `STORAGE_BACKEND` | 文件存储：`local`（默认）、`cos`、`oss`，对应配置见 `.env.example` 与[部署指南](../docs/deployment.md#对象存储腾讯云-cos--阿里云-oss) |
| `VISION_MODEL` | 看图模型，AI 生成答案时含配图的题改用该模型，见[部署指南](../docs/deployment.md#大模型看图) |
| `AUTO_COMMIT` | 解析完成后自动入库确定的题，默认 true；阈值等见 `.env.example` |

## 流水线

```
上传（/api/uploads → PUT /api/files/…）→ 创建任务 → 进程内队列
  ocr        文件归一化（多张图片合成 PDF）→ 解析引擎链 → IR（parse_block）+ 页面图
  classify   标题规则 + 大模型 → 学段 / 学科 / 年级 / 类型 / 地区 / 学年 / 教材
  segment    规则切分 + 大模型分组（只返回单元 id）→ 草稿题（draft_question）
  knowledge  按知识树标注知识点（每题 1–3 个），同一次调用评估难度；未配置大模型时跳过，核对页可补标
  difficulty 难度系数 0–1，越高越难：0.7 × 大模型评估 + 0.3 × 按题位的基线，可校准
  dedupe     与校本题库查重（字面相似度，可叠加向量语义相似度），标记疑似重复
```

| 模块 | 说明 |
|---|---|
| `app/pipeline/parsers/mineru_cloud.py` | v4 批量上传接口：申请上传地址 → PUT → 轮询 → 下载 zip → `content_list.json` 转 IR |
| `app/pipeline/parsers/lite.py` | PyMuPDF 读取文字层，按行输出；按字号与基线识别上下标（O₂、x²），x^2、a_n、√(…) 转为上下标或 LaTeX；分式等二维公式仍为文字，扫描件、Word 不可用 |
| `app/pipeline/parsers/router.py` | 引擎链与降级 |
| `app/pipeline/segment.py` | 大题标题 / 题号 / 选项 / 分值 / 卷末答案的规则切分；大模型分组校验与置信度 |
| `app/pipeline/classify.py` | 试卷分类 |
| `app/pipeline/llm.py` | OpenAI 兼容 `/chat/completions` 客户端（JSON 输出） |
| `app/worker.py` | 任务队列：未配置 Redis 时为进程内队列；配置后为 Redis Streams 消费组（去重、续约、失联接手、启动时补排），`python -m app.worker` 启动独立 Worker |
| `app/storage.py` | 文件存储：本地磁盘、腾讯云 COS、阿里云 OSS |

### 知识树、难度模型与评测（P2）

- `app/knowledge_tree.py`：知识树导入（JSON / CSV）、内置知识树同步（`app/data/knowledge/`）、按试卷选树、节点检索。
- `scripts/build_knowledge_trees.py`：由《基础学科知识点总纲》生成 23 个学段学科的内置知识树：

  ```bash
  uv run python scripts/build_knowledge_trees.py ../outputs/基础学科知识点总纲/知识点总纲.md --concurrency 8
  ```
- `app/pipeline/knowledge.py`：按知识树标注知识点（小树整表选择 / 大树检索候选后选择 / 无树自由生成），同一次调用评估难度。
- `app/pipeline/difficulty.py`：大模型评估与基线加权，可用评测拟合的校准直线修正。
- `app/evaluation.py`：以核对结果为标准答案重新解析并计算指标；页面在「试卷解析 › 解析评测」。

### 校本题库选题与试卷库

- `app/chapters.py`：内置教材章节目录（`app/data/chapters/*.json`，版本 → 册 → 章 → 节），每节的 `knowledge` 为对应的知识点名称；
  按章节筛选时，题目知识点（含各级上级名称）属于该节 `knowledge` 的即归入该节，题目无需单独标注章节。
- `scripts/crawl_textbooks.py`：从国家中小学智慧教育平台（同步课堂）抓取厦门市现用版本的教材目录并生成上述文件，只取目录标题；
  版本清单见脚本中的 `XIAMEN`，依据《厦门市小初高教材 PDF 检索与下载入口》。每节对应的知识点保留手工校订与大模型结果，其余用大模型
  （`--no-thinking` 关闭思考）或名称匹配生成：

  ```bash
  uv run python scripts/crawl_textbooks.py --no-thinking            # 抓取（缓存在 data/cache/smartedu）并生成
  uv run python scripts/crawl_textbooks.py --offline --only 高中数学  # 只用缓存重新生成某学科
  ```

- `app/bank.py`：按学段学科、知识点（含下级，节点 id 或路径匹配）、题型、难度、试卷类型、年份、关键词检索入库题；
  知识树各节点的题数；按解析任务聚合的试卷库（含年级、学科等 facets）。
- 普通题库查询在数据库中计数和分页；知识点、试卷类型、年份、关键词等复杂筛选每批读取 200 行必要字段，
  只加载当前页的完整题目。单校题量达到数万后，仍需改为「题目—知识点」关联表和数据库索引，减少扫描时间。
- 修改试卷分类或再次入库时，同步更新已入库题目的分类快照（`sync_meta`）。
- 入库前查重（`check_duplicates`）：相同文件、同名试卷或多数题目已入库时整卷不保存；单题与题库重复时跳过，老师确认后可强制保存。
  `DELETE /api/papers/{id}` 把重复入库的试卷移出试卷库。

### 相似题与批量解析

- `app/similar.py`：字面 + 可选语义相似度；解析时查重，核对页与 `/api/similar/search` 查询相似题。
- `app/api/batches.py`：批量上传，每份试卷一个任务，由 worker 并发处理（Worker 数 × `WORKER_CONCURRENCY`）；支持取消与重试。

### AI 生成答案

核对页的「AI 生成答案」按钮或单题的「AI 解答」触发（`app/pipeline/answer.py`），后台逐题调用大模型，
结果标记为 AI 生成并附提示（如含图题答案可能不准确），老师修改后变为人工修改。

### 数据库迁移

启动时 `init_db()` 会给已有表补上新增的可空列（轻量迁移，只加列）。改列、删列或上线 PostgreSQL 前需换成 Alembic。

### AI 用量与成本

每次大模型 / MinerU 调用写入 `ai_usage` 表（`app/usage.py`），失败的调用也会记录。费用在查询时按当前单价计算，
所以单价可以事后补填或调整，历史数据随之重算。单价请以厂商官网为准；未配置时只统计 tokens 与页数。

### 置信度

从 0.95 起按规则扣分：题号不连续、单选题选项数不是 4、选择题无选项、题干过短、分值为默认值、未识别到答案、含乱码。
启用大模型时，与规则切分一致 +0.03，不一致 −0.25。低于 0.8 进入「待核对」。

## 测试

```bash
uv run ruff check .   # 代码检查，规则见 pyproject.toml [tool.ruff]；--fix 自动修复
uv run pytest
```

需要 PostgreSQL：默认使用 docker compose 中数据库服务器上的 `fg_quiz_test` 库（不存在时自动创建，先 `docker compose up -d db`），
不影响开发用的 `fg_quiz` 库。也可用 `TEST_DATABASE_URL` 指定；测试会清空重建库中的表，库名须以 `_test` 结尾。
任务队列测试默认用 fakeredis，设置 `TEST_REDIS_URL` 时连接真实 Redis（CI 两者都连真实服务）。

测试不访问外部服务：MinerU 与大模型通过 respx / MockTransport 模拟，端到端测试使用生成的电子版试卷（`tests/fixtures.py`）。

## 资源占用

- 本地上传边接收边写临时文件，超限、断连或取消时清理，完成后原子发布；计算本地文件指纹也分块读取。
- PDF 页面图每批渲染和上传 4 页，释放上一批后再处理下一批；每批显式关闭文档。
- 相似题候选每批读取 200 行，只保留前 `limit` 个命中；字面特征缓存最多 256 条，超过 2000 字符的题不缓存。
- AI 组卷的候选只读取题干、题型、难度、知识点，选中后再读取答案、解析和配图。
- 进度订阅运行中每 2 秒检查、排队中每 5 秒检查，取消后结束；前端离开解析或评测页面时停止订阅与轮询。

小内存机器可在 `server/.env` 设置 `WORKER_CONCURRENCY=1`、`ANSWER_CONCURRENCY=1`、`PAGE_DPI=80`、
`MAX_FILE_MB=20`。这会降低并行处理量、原图清晰度和上传大小上限；修改配置后需重启对应服务。
每个 Worker 的答案请求并发可达 `WORKER_CONCURRENCY × ANSWER_CONCURRENCY`，增加 Worker 数也会按比例增加占用。

这些改动限制了分页、结果缓存和页面图的内存增长；解析引擎仍会读取完整源文件，复杂筛选与相似题仍需遍历候选，
不能替代真实数据的负载测试。资源边界回归见 `tests/test_resources.py`。

## 当前限制（后续阶段）

- 内置知识树按知识领域组织（非教材章节），学校有正式知识体系时建议导入；相似题阈值需用真实题库校准。
- 难度模型尚无学生作答数据，校准依赖老师调整过难度的评测样本。
- 难度系数含义已改为「越高越难」，启动时会把旧数据一次性换算为 1 − 旧值（`app_meta.coef_semantics`）。
- 本地 MinerU（`mineru_local`）：P3；Word 在未配置 MinerU 时无法解析（需要 LibreOffice 转换，P3）。
- 大题标题后、第一题前的材料（如阅读材料）当前被视为标题说明而不归入题目，语文 / 英语试卷需在 P2 处理。
- 轻量引擎按坐标排序，双栏试卷的阅读顺序可能错乱；双栏试卷请使用 MinerU。
