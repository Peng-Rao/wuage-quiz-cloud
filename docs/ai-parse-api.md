# 试卷解析接口契约（P0）

前端类型定义：`src/api/parse/types.ts`（以此为准，本文档是其说明）。
前端默认使用内存 mock（`src/api/parse/mock.ts`）；设置 `VITE_PARSE_API=http` 后切换为 `src/api/parse/http.ts` 对接本文接口。

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
| `knowledge` | 知识点标注（从知识树候选中选择） | `11 个知识点` |
| `difficulty` | 难度评估 | `预估得分率` |

`progress` 由后端给出（0–100），前端把上传进度映射到总进度的前 10%。
`parser` 返回实际使用的引擎：`mineru_cloud` / `mineru_local` / `lite`（含降级结果）。

### 草稿题编辑语义

- `PATCH` 修改 `stem / options / answer / type` 视为人工核对，后端将 `confidence` 置为 `1`。
- `merge-previous`：与上一题合并，`blockIds`、`regions` 拼接，分值相加，知识点取并集，难度按分值加权，置信度取较低者；第 1 题调用返回 400。
- `split`：按「（1）/(1)/⑴」小问标记拆分，公共题干复制到每个小问，分值均分（余数给最后一问）；无标记时返回 400。
- 合并 / 拆分会产生新的题目 id，前端以返回的完整列表为准。
- `commit` 只把选中题目标记为 `saved`，可多次调用。

### 前端未覆盖（P1 之后）

- 知识点修改（需要知识树接口）、公式编辑器、查重命中后的「合并到已有题」操作。
- 刷新页面后恢复进行中的任务（需要把 jobId 放进路由，mock 为内存存储，刷新即丢失）。
