# 福格云上题库 · 解析服务（P1）

FastAPI 实现的试卷解析后端，接口契约见 [docs/ai-parse-api.md](../docs/ai-parse-api.md)。

## 启动

```bash
cd server
uv sync
cp .env.example .env        # 填写 MINERU_TOKEN、LLM_*（可先不填）
uv run uvicorn app.main:app --port 8000 --reload
```

前端连接本服务（Vite 把 `/api` 代理到 8000 端口）：

```bash
npm run dev:api
```

`GET /api/health` 可查看当前启用了哪些引擎：`{"mineru": true, "llm": true, ...}`。

## 配置

| 变量 | 说明 |
|---|---|
| `PARSER_CHAIN` | 解析引擎链，默认 `["mineru_cloud","lite"]`：未配置或失败时依次降级 |
| `MINERU_TOKEN` | MinerU 云端 Token（https://mineru.net/apiManage），未填时跳过云端 |
| `MINERU_MODEL_VERSION` | `vlm`（默认，公式更准）或 `pipeline` |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容接口，三项齐全才启用；未配置时只用规则拆题 |
| `LLM_EXTRA_BODY` | 附加到请求体的厂商参数（JSON），如 `{"enable_thinking": false}` |
| `WORKER_CONCURRENCY` | 同时解析的试卷数，批量上传时其余排队，默认 2 |
| `EMBEDDING_MODEL` | 相似题语义检索的向量模型（如 `text-embedding-v4`），留空则只用字面相似度 |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` | 向量接口地址与 Key，留空沿用 `LLM_BASE_URL` / `LLM_API_KEY` |
| `ANSWER_CONCURRENCY` | AI 生成答案时同时进行的请求数，默认 3 |
| `LLM_PRICES` | 成本估算单价，按模型名，元 / 百万 tokens：`{"qwen-plus": {"input": 0.8, "output": 2, "cached_input": 0.16}}` |
| `MINERU_PRICE_PER_PAGE` | MinerU 单价，元 / 页 |
| `CURRENCY` | 金额前缀，默认 `¥` |
| `DATABASE_URL` | 默认 `data/app.db`（SQLite） |

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
| `app/worker.py` | 进程内队列，重启后恢复未完成任务 |
| `app/storage.py` | 对象存储抽象，当前为本地磁盘 |

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

- `app/bank.py`：按学段学科、知识点（含下级，节点 id 或路径匹配）、题型、难度、试卷类型、年份、关键词检索入库题；
  知识树各节点的题数；按解析任务聚合的试卷库（含年级、学科等 facets）。
- 知识点、试卷类型、年份、关键词在内存中筛选，单校题量达到数万后需改为「题目—知识点」关联表。
- 修改试卷分类或再次入库时，同步更新已入库题目的分类快照（`sync_meta`）。
- 入库前查重（`check_duplicates`）：相同文件、同名试卷或多数题目已入库时整卷不保存；单题与题库重复时跳过，老师确认后可强制保存。
  `DELETE /api/papers/{id}` 把重复入库的试卷移出试卷库。

### 相似题与批量解析

- `app/similar.py`：字面 + 可选语义相似度；解析时查重，核对页与 `/api/similar/search` 查询相似题。
- `app/api/batches.py`：批量上传，每份试卷一个任务，由 worker 按 `WORKER_CONCURRENCY` 并发处理；支持取消与重试。

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
uv run pytest
```

测试不访问外部服务：MinerU 与大模型通过 respx / MockTransport 模拟，端到端测试使用生成的电子版试卷（`tests/fixtures.py`）。

## 当前限制（后续阶段）

- 内置知识树按知识领域组织（非教材章节），学校有正式知识体系时建议导入；相似题阈值需用真实题库校准。
- 难度模型尚无学生作答数据，校准依赖老师调整过难度的评测样本。
- 难度系数含义已改为「越高越难」，启动时会把旧数据一次性换算为 1 − 旧值（`app_meta.coef_semantics`）。
- 本地 MinerU（`mineru_local`）：P3；Word 在未配置 MinerU 时无法解析（需要 LibreOffice 转换，P3）。
- 队列为进程内实现，多实例部署需换成 Redis + 独立 Worker；存储需换成 OSS / S3 预签名直传。
- 未接入账号体系，所有数据归属 `demo` 学校。
- 大题标题后、第一题前的材料（如阅读材料）当前被视为标题说明而不归入题目，语文 / 英语试卷需在 P2 处理。
- 轻量引擎按坐标排序，双栏试卷的阅读顺序可能错乱；双栏试卷请使用 MinerU。
