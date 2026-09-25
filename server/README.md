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
  knowledge  P2（当前为「已跳过」）
  difficulty 基线估计（同题型内按题位递减），P2 替换
```

| 模块 | 说明 |
|---|---|
| `app/pipeline/parsers/mineru_cloud.py` | v4 批量上传接口：申请上传地址 → PUT → 轮询 → 下载 zip → `content_list.json` 转 IR |
| `app/pipeline/parsers/lite.py` | PyMuPDF 读取文字层，按行输出；扫描件、Word 不可用 |
| `app/pipeline/parsers/router.py` | 引擎链与降级 |
| `app/pipeline/segment.py` | 大题标题 / 题号 / 选项 / 分值 / 卷末答案的规则切分；大模型分组校验与置信度 |
| `app/pipeline/classify.py` | 试卷分类 |
| `app/pipeline/llm.py` | OpenAI 兼容 `/chat/completions` 客户端（JSON 输出） |
| `app/worker.py` | 进程内队列，重启后恢复未完成任务 |
| `app/storage.py` | 对象存储抽象，当前为本地磁盘 |

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

- 知识点标注、难度模型、题库查重：P2 / P4。
- 本地 MinerU（`mineru_local`）：P3；Word 在未配置 MinerU 时无法解析（需要 LibreOffice 转换，P3）。
- 队列为进程内实现，多实例部署需换成 Redis + 独立 Worker；存储需换成 OSS / S3 预签名直传。
- 未接入账号体系，所有数据归属 `demo` 学校。
- 大题标题后、第一题前的材料（如阅读材料）当前被视为标题说明而不归入题目，语文 / 英语试卷需在 P2 处理。
- 轻量引擎按坐标排序，双栏试卷的阅读顺序可能错乱；双栏试卷请使用 MinerU。
