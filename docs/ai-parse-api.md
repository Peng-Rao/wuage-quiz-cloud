# 试卷解析接口契约

前端类型定义：`src/api/parse/types.ts`（以此为准，本文档是其说明）。
前端默认使用内存 mock（`src/api/parse/mock.ts`）；`npm run dev:api`（即 `VITE_PARSE_API=http`）切换为 `src/api/parse/http.ts`，对接 `server/` 中的实现（见 [server/README.md](../server/README.md)）。

## 约定

- JSON，字段 camelCase；时间为 ISO 8601。
- 失败时返回非 2xx，响应体 `{ "message": "给用户看的中文错误" }`。
- `bbox` 为相对页面宽高归一化的 `[x0, y0, x1, y1]`（0–1），与 MinerU `content_list.json` 换算后一致。
- `confidence` 为 0–1，低于 `0.8` 的题进入「待核对」。
- `coef` 为难度系数（预估得分率 0–1）：≥ 0.7 容易，≥ 0.4 适中，其余较难。

## 流程

```
POST /api/uploads            （每个文件一次）→ { uploadUrl, fileKey }
PUT  {uploadUrl}             浏览器直传对象存储
POST /api/parse-jobs         → ParseJob（status=queued）
GET  /api/parse-jobs/{id}/events   SSE，每条 data 为 ParseJob 快照，done / failed 后服务端关闭
GET  /api/parse-jobs/{id}/questions → DraftQuestion[]
……核对（PATCH / merge / split / source / meta）
POST /api/parse-jobs/{id}/commit    → 入校本题库
```

## 接口

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/uploads` | `{ fileName, fileSize, contentType }` | `{ uploadUrl, fileKey }` |
| POST | `/api/parse-jobs` | `{ fileKeys: string[], fileNames: string[], options: ParseOptions }` | `ParseJob` |
| GET | `/api/parse-jobs/{id}` | — | `ParseJob` |
| GET | `/api/parse-jobs/{id}/events` | SSE | `data: ParseJob` |
| GET | `/api/parse-jobs?recent=1` | — | `RecentUpload[]`（最近 5 条） |
| PUT | `/api/parse-jobs/{id}/meta` | `PaperMeta` | `PaperMeta` |
| GET | `/api/parse-jobs/{id}/questions` | — | `DraftQuestion[]`（按 `no` 升序） |
| PATCH | `/api/draft-questions/{qid}` | `DraftQuestionPatch` | `DraftQuestion` |
| POST | `/api/draft-questions/{qid}/merge-previous` | — | `DraftQuestion[]`（重排后的完整列表） |
| POST | `/api/draft-questions/{qid}/split` | — | `DraftQuestion[]`（重排后的完整列表） |
| GET | `/api/draft-questions/{qid}/source` | — | `SourceImage[]` |
| POST | `/api/parse-jobs/{id}/commit` | `{ questionIds: string[] }` | `{ savedCount }` |
| POST | `/api/parse-jobs/{id}/generate-answers` | `{ questionIds?: string[], overwrite?: boolean }` | `AnswerTask`（202） |
| GET | `/api/parse-jobs/{id}/usage` | — | `JobUsage`（汇总 + 每次调用明细） |
| GET | `/api/usage/summary?days=30` | — | `UsageOverview`（近 N 天用量与平均成本） |

### 本地存储（P1）

后端使用本地磁盘存储时，`uploadUrl` 为 `/api/files/uploads/…`（同源 PUT，请求体为文件原始字节）；
页面图与题图通过 `GET /api/files/jobs/…` 读取。换成 OSS / S3 后 `uploadUrl` 为预签名地址，前端代码不变。

### 上传校验

- 一次解析任务：1 个 `.pdf` / `.docx`，或 1–N 张 `.jpg/.png`（按上传顺序作为连续页）。
- 单个文件 ≤ 50 MB；PDF ≤ 200 页（MinerU 云端限制）。

### ParseJob.stages

固定 5 个阶段，顺序即执行顺序；`options.knowledge=false` 时 `knowledge` 为 `skipped`。

| stage | 含义 | 完成后 note 示例 |
|---|---|---|
| `ocr` | 版面识别与文字提取（MinerU / 本地引擎） | `识别 4 页` |
| `classify` | 学段 / 学科 / 类型分类，完成后写入 `meta` | `高中 · 数学 · 期中` |
| `segment` | 题目切分、题型判断、答案关联 | `9 道题` |
| `knowledge` | 知识点标注（从知识树候选中选择）；P1 固定为 `skipped` | `11 个知识点` |
| `difficulty` | 难度评估；P1 为基线估计 | `预估得分率` / `基线估计` |

`segment` 未启用大模型时 note 带「（规则）」后缀，如 `9 道题（规则）`。

`progress` 由后端给出（0–100），前端把上传进度映射到总进度的前 10%。
`parser` 返回实际使用的引擎：`mineru_cloud` / `mineru_local` / `lite`（含降级结果）。

### DraftQuestion.images

题目内配图（几何图、函数图像、表格截图）的访问地址数组，按原卷顺序；没有配图时为空数组。
题干、选项、答案中的公式以 `$...$`（行内）/ `$$...$$`（独立）包裹的 LaTeX 表示，前端用 KaTeX 渲染。

### 草稿题编辑语义

- `PATCH` 修改 `stem / options / answer / type` 视为人工核对，后端将 `confidence` 置为 `1`。
- `merge-previous`：与上一题合并，`blockIds`、`regions` 拼接，分值相加，知识点取并集，难度按分值加权，置信度取较低者；第 1 题调用返回 400。
- `split`：按「（1）/(1)/⑴」小问标记拆分，公共题干复制到每个小问，分值均分（余数给最后一问）；无标记时返回 400。
- 合并 / 拆分会产生新的题目 id，前端以返回的完整列表为准。
- `commit` 只把选中题目标记为 `saved`，可多次调用。

### AI 生成答案

- 由老师在核对页手动触发，不在解析流程中自动执行（避免不可控的费用）。
- 不传 `questionIds` 时处理本卷所有缺少答案的题；`overwrite=false`（默认）时跳过已有答案的题。
- 后台排队执行，每题一次大模型调用，并发数由 `ANSWER_CONCURRENCY` 控制（默认 3）；进度见 `ParseJob.answerTask`
  （`status / total / done / failed / questionIds`），前端轮询 `GET /api/parse-jobs/{id}`。同一试卷同时只能有一个任务，重复发起返回 409。
- `DraftQuestion.answerSource`：`paper` 原卷识别 / `ai` 大模型生成 / `manual` 人工修改（PATCH `answer` 或 `analysis` 后变为 `manual`）。
- `DraftQuestion.answerNote`：AI 答案的提示，如题目含图（大模型看不到图片）、模型自认不确定、选择题答案不符合题型。
- 入校本题库时保留答案来源；用量记为 `purpose=answer`。

### AI 用量与成本

- 每次大模型调用记录输入 / 输出 / 思考 / 缓存命中 tokens 与耗时；MinerU 按解析页数记录（解析完成即记，下载结果失败也计入）。
- 大模型流式请求带 `stream_options.include_usage`，从最后一个数据块读取用量；服务端未返回时按字符数估算，`estimated=true`。
- 失败的调用同样记录（`status=error`），多数厂商对失败请求仍按输入计费。
- 费用不落库，查询时按服务端配置的单价计算（`LLM_PRICES` 按模型名，元 / 百万 tokens；`MINERU_PRICE_PER_PAGE` 元 / 页）。
  未配置单价时 `cost=null`；部分缺失时 `priced=false`，`unpricedModels` 列出缺少单价的模型。
- `ParseJob.usage` 为本任务的 `UsageSummary`，随 SSE 推送；`UsageOverview` 给出每份试卷、每页、每题的平均成本，用于估算后续费用。

### 尚未覆盖

- 知识点修改（需要知识树接口）、公式编辑器、查重命中后的「合并到已有题」操作。
- 刷新页面后恢复进行中的任务（需要把 jobId 放进路由，mock 为内存存储，刷新即丢失）。
