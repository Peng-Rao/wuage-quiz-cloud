# 试卷解析接口契约

前端类型定义：`src/api/parse/types.ts`（以此为准，本文档是其说明）。
前端默认使用内存 mock（`src/api/parse/mock.ts`）；`npm run dev:api`（即 `VITE_PARSE_API=http`）切换为 `src/api/parse/http.ts`，对接 `server/` 中的实现（见 [server/README.md](../server/README.md)）。

## 约定

- JSON，字段 camelCase；时间为 ISO 8601。
- 失败时返回非 2xx，响应体 `{ "message": "给用户看的中文错误" }`。
- `bbox` 为相对页面宽高归一化的 `[x0, y0, x1, y1]`（0–1），与 MinerU `content_list.json` 换算后一致。
- `confidence` 为 0–1，低于 `0.8` 的题进入「待核对」。
- `coef` 为难度系数 0–1，越高越难，1 为最难（约等于 1 − 预估得分率）：≤ 0.3 容易，≤ 0.6 适中，其余较难。

## 流程

```
POST /api/uploads            （每个文件一次）→ { uploadUrl, uploadHeaders, fileKey }
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
| POST | `/api/uploads` | `{ fileName, fileSize, contentType }` | `{ uploadUrl, uploadHeaders, fileKey }` |
| POST | `/api/parse-jobs` | `{ fileKeys: string[], fileNames: string[], options: ParseOptions }` | `ParseJob` |
| GET | `/api/parse-jobs/{id}` | — | `ParseJob` |
| GET | `/api/parse-jobs/{id}/events` | SSE | `data: ParseJob` |
| GET | `/api/parse-jobs?recent=1` | — | `RecentUpload[]`（最近 5 条） |
| GET | `/api/parse-jobs?status=queued,running&batchId=&limit=20&offset=0` | — | `JobListPage`（任务列表，最新在前） |
| POST | `/api/parse-jobs/{id}/cancel` | — | `ParseJob`（仅排队中的任务） |
| POST | `/api/parse-jobs/{id}/retry` | — | `ParseJob`（失败或已取消的任务重新排队） |
| POST | `/api/parse-batches` | `{ items: [{ fileKeys, fileNames }], options }` | `ParseBatch` |
| GET | `/api/parse-batches/{id}` | — | `ParseBatch`（含各状态计数与任务列表） |
| GET | `/api/draft-questions/{qid}/similar?limit=5&scope=all` | — | `SimilarQuestion[]` |
| POST | `/api/parse-jobs/{id}/tag-knowledge` | — | `ParseJob`（202，补标缺少知识点的题） |
| GET | `/api/knowledge-trees` | — | `KnowledgeTree[]` |
| POST | `/api/knowledge-trees/import` | `{ format: 'json' \| 'csv', content, name?, subject?, stage?, textbook? }` | `KnowledgeTree` |
| GET / DELETE | `/api/knowledge-trees/{id}` | — | `KnowledgeTreeDetail`（含嵌套节点）/ 204 |
| GET | `/api/knowledge/search?q=&jobId=&treeId=` | — | `KnowledgeNodeHit[]`（知识点联想） |
| POST | `/api/parse-jobs/{id}/eval-sample` | — | `EvalSample`（核对结果设为评测样本，重复调用覆盖） |
| GET / DELETE | `/api/eval-samples`、`/api/eval-samples/{id}` | — | `EvalSample[]` / 204 |
| POST | `/api/eval-runs` | `{ sampleIds? }` | `EvalRun`（202，后台运行） |
| GET | `/api/eval-runs`、`/api/eval-runs/{id}` | — | `EvalRun` |
| POST | `/api/eval-runs/{id}/apply-calibration` | — | 采用拟合出的难度校准 |
| GET / DELETE | `/api/difficulty-calibration` | — | 当前校准 / 取消校准 |
| POST | `/api/similar/search` | `{ text, type?, limit?, scope? }` | `SimilarQuestion[]` |
| PUT | `/api/parse-jobs/{id}/meta` | `PaperMeta` | `PaperMeta` |
| GET | `/api/parse-jobs/{id}/questions` | — | `DraftQuestion[]`（按 `no` 升序） |
| PATCH | `/api/draft-questions/{qid}` | `DraftQuestionPatch` | `DraftQuestion` |
| POST | `/api/draft-questions/{qid}/merge-previous` | — | `DraftQuestion[]`（重排后的完整列表） |
| POST | `/api/draft-questions/{qid}/split` | — | `DraftQuestion[]`（重排后的完整列表） |
| GET | `/api/draft-questions/{qid}/source` | — | `SourceImage[]` |
| POST | `/api/parse-jobs/{id}/commit` | `{ questionIds: string[], force?: boolean }` | `CommitResult`（`savedCount / savedIds / skipped / duplicatePaper`） |
| POST | `/api/parse-jobs/{id}/generate-answers` | `{ questionIds?: string[], overwrite?: boolean }` | `AnswerTask`（202） |
| GET | `/api/parse-jobs/{id}/usage` | — | `JobUsage`（汇总 + 每次调用明细） |
| GET | `/api/usage/summary?days=30` | — | `UsageOverview`（近 N 天用量与平均成本） |
| GET | `/api/bank/questions?stage=&subject=&nodeId=&chapterId=&type=&diff=&paperType=&year=&region=&grade=&term=&q=&paperId=&sort=&limit=&offset=` | — | `{ items: BankQuestion[], total }`（校本题库选题） |
| GET | `/api/bank/knowledge-counts?treeId=` | — | `{ [nodeId]: number }`（各知识点含下级的入库题数） |
| GET | `/api/bank/facets?stage=&subject=` | — | `QuestionFacets`（地区、年级、年份的可选值与题数） |
| GET | `/api/chapters?stage=&subject=` | — | `TextbookVersion[]`（教材版本 → 册 → 章 → 节） |
| GET | `/api/bank/chapter-counts?bookId=` | — | `{ [chapterOrSectionId]: number }`（某册各章、节的入库题数） |
| GET | `/api/papers?stage=&grade=&subject=&textbook=&paperType=&category=&q=&limit=&offset=` | — | `PaperPage`（试卷库，含各维度 facets） |
| GET | `/api/papers/{id}` | — | `PaperDetail`（按原卷题号排列的题目） |
| DELETE | `/api/papers/{id}` | — | 204（移出试卷库：删除已入库的题，草稿题恢复为未保存） |

### 文件存储

后端使用本地磁盘存储时，`uploadUrl` 为 `/api/files/uploads/…`（同源 PUT，请求体为文件原始字节）；
页面图与题图通过 `GET /api/files/jobs/…` 读取。使用腾讯云 COS（`STORAGE_BACKEND=cos`）或阿里云 OSS（`oss`）时
`uploadUrl` 为预签名地址，PUT 时须原样携带 `uploadHeaders`（`Content-Type`、`x-cos-forbid-overwrite` /
`x-oss-forbid-overwrite` 等参与签名），不携带 Cookie；`GET /api/files/jobs/…` 校验权限后 302 跳转到短时有效的签名地址。

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
| `knowledge` | 知识点标注（大模型，每题 1–3 个）；未启用大模型或关闭选项时为 `skipped` | `11 个知识点` |
| `difficulty` | 难度评估；P1 为基线估计 | `基线估计（越高越难）` |
| `dedupe` | 与校本题库查重；`options.dedupe=false` 时为 `skipped` | `2 道疑似重复` / `未发现重复` |

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

### 知识树（P2）

- 导入格式：JSON（`{"nodes": [{"name", "aliases", "children"}]}`，可带 name / subject / stage / textbook）或 CSV（每行一条路径，
  列为各级名称，可选「别名」列，多个用 `|` 分隔；首行为表头时自动跳过）。CSV 必须提供学科与学段。
- 内置知识树：由《小学·初中·高中基础学科知识点总纲》整理，覆盖 23 个学段学科（模块 → 主题 → 知识点），
  由 `server/scripts/build_knowledge_trees.py` 生成（大模型按原文细目整理知识点名称，校验失败时规则拆分）。
  启动时同步：新增缺少的、替换内容有变化的、删除已不存在的；学校导入的知识树不受影响。
- 解析时按学科、学段选树：学校导入的优先于内置知识树，教材版本一致的优先，其次最新导入的。
- 标注：末级知识点 ≤ 300 个时把完整清单交给大模型按编号选择；更大的树先生成名称、再检索候选、最后选择。
  清单中没有的知识点保留名称并标记 `inTree=false`。`KnowledgePointRef` 增加 `path`、`inTree`。
- 老师编辑知识点时，名称（忽略「1.3」等编号）或别名与知识树节点一致则归入该节点。

### 难度模型（P2）

- 最终系数 = 校准(0.7 × 大模型评估 + 0.3 × 按题位的基线)，截断到 0.02–0.98。大模型评估与知识点标注同一次调用给出。
- `DraftQuestion.difficultySource`：`baseline` / `ai` / `manual`（PATCH 修改 `coef` 后为 `manual`）。
- 校准：评测中老师调整过难度的题（≥ 5 道）拟合 `y = a·x + b`，采用后对之后解析的试卷生效。

### 解析评测（P2）

- 老师在核对页「设为评测样本」：保存当前的分类、题型、选项、答案（含来源）、知识点、难度作为标准答案。
- 运行评测：对每个样本的原文件重新完整解析（评测任务 `kind=eval`，不出现在任务列表、不参与查重、用量照常计入），
  按题干相似度配对后计算：拆题查准率 / 查全率、题型、选项数、答案（只统计原卷或老师填写的）准确率、
  知识点前 3 命中率与召回率、难度平均误差（只统计老师调整过的）、试卷分类准确率。
- `EvalRun.config` 记录当次的解析引擎、模型与校准，便于对比不同配置。

### 题目出处与知识点

- `DraftQuestion.source`：出处，由试卷分类（学年、地区、年级、试卷类型、试卷名称）与题号实时拼出，`label` 如
  「2026—2027 上 · 北京 · 海淀 · 高一期中考试《…》第 3 题」；修改分类后随之更新。入库时保存文件名、题号、页码与分类。
- `PaperMeta.title`：试卷名称，取自卷首标题（规则 + 大模型）。
- 知识点：大模型按学科与教材为每题标注 1–3 个，名称规范化后按「学科 + 名称」生成 id（同名知识点 id 一致）。
  目前没有正式知识树，名称由大模型生成；接入知识树后改为从候选中选择。PATCH `knowledgePoints` 时后端同样统一 id。
- PATCH 只处理实际变化的字段：答案或解析未变时不会改变 `answerSource`。
- `SimilarQuestion.origin` / `knowledgePoints`：相似题的出处与知识点。

### 批量后台解析

- 一次上传多份试卷：每一项（1 个 PDF / Word，或同一份试卷的多张图片）各自创建解析任务，`ParseJob.batchId` 为所属批次。
  任一项校验失败则整批不创建。
- 任务在服务端排队，同时解析的份数由 `WORKER_CONCURRENCY` 控制（默认 2）；关闭页面不影响解析，服务重启后未完成的任务自动恢复。
- 排队中的任务可取消（`status=cancelled`）；失败或已取消的任务可重试（草稿题清空重建）。
- 前端：多份 PDF / Word 自动走批量；「解析任务」列表在有进行中任务时每 2 秒刷新；页面地址 `?job=` 指向当前试卷。

### 相似题

- 字面相似度：归一化（去空白、标点、填空线、LaTeX 命令，全角转半角）后的二元 / 三元字组合余弦，默认启用，无外部依赖。
- 语义相似度（可选）：配置 `EMBEDDING_MODEL` 后调用 OpenAI 兼容 `/embeddings`，与字面分加权；解析时为每题计算向量，入库时一并保存。
- `score` ≥ 0.85 视为「疑似重复」（`duplicate=true`），查重阶段据此设置 `DraftQuestion.duplicateOf`（指向校本题库题目）。阈值为经验值，接入真实题库后需校准。
- 只与同题型比较；`scope=all` 时还包含其他试卷中尚未入库的草稿题（用于发现重复上传），不包含本卷。
- 向量调用计入 AI 用量（`purpose=embed` / `similar`）。当前为全量比对，适合万题以内；题量更大时换成 pgvector 等向量索引。

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

### 校本题库选题与试卷库

前端类型见 `src/api/bank/types.ts`，与 `VITE_PARSE_API` 共用 mock / http 开关。

- `nodeId`：知识点节点，含所有下级；逗号分隔多个时含任一即可。`chapterId`：教材章或节，题目知识点（含各级上级名称）属于该章节对应的知识点即归入。
- `region`：省级地区（「北京 · 海淀」按「北京」）；`term`：学期 `上` / `下`（取自学年或试卷名称）；`paperType`：场景关键词，匹配试卷类型或名称。
- `nodeId` 匹配：题目的知识点按节点 id 或路径匹配——内置知识树更新后节点 id 会重建，路径不变，已入库的题仍能归入节点。
- `diff`：`容易`（系数 ≤ 0.3）/ `适中`（≤ 0.6）/ `较难`；`paperType`：试卷类型关键词，逗号分隔，含任一即可（如 `期中,期末`）；
  `year`：`2026` 表示学年或试卷名称中含该年份，`<2024` 表示更早；`sort`：`default`（按试卷、题号）/ `latest` / `easy` / `hard`。
- 试卷库中的一份试卷 = 同一解析任务入库的题，`PaperSummary.id` 为任务 id；`sourceQuestionCount > questionCount` 表示只入库了部分题。
  `facets` 给出学段、年级、学科、类型各自在其余筛选条件下的试卷数，没有分类的记为「未分类」。
- 修改试卷分类（`PUT /meta`）或再次入库时，同步更新已入库题目的分类快照，选题按最新分类筛选。
- 试题篮在前端保存题目快照、题序与分值（localStorage），不依赖后端。

### 重复入库检查

`commit` 默认（`force=false`）先检查重复，本卷自己已入库的题不算重复：

- 整份试卷已在试卷库中时**不保存任何题**，返回 `duplicatePaper`：`same_file` 文件内容相同（`ParseJob.file_hash`，旧任务入库时补算）、
  `same_title` 同学段学科的同名试卷（名称至少 8 个字）、`most_questions` 本次选中的题有一半以上（且至少 3 题）与同一份已入库试卷重复。
- 否则逐题与校本题库比对（相似度 ≥ 0.85），重复的题跳过并列在 `skipped` 中（含已有题目的来源），其余正常保存。
- 老师确认后以 `force=true` 重新提交（整卷，或只提交 `skipped` 中的题）即全部保存。

### 尚未覆盖

- 查重命中后的「合并到已有题」操作。
- 轻量解析引擎（lite）不识别分式、矩阵等二维公式与图片中的公式，需要 MinerU。
- mock 为内存存储，刷新页面后任务丢失（连接后端时刷新可恢复）。
