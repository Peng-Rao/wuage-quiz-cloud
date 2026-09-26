/**
 * 试卷解析接口契约（P0）
 *
 * 前端 mock 与后端实现共用这一份定义，字段变更需同步 docs/ai-parse-api.md。
 * 所有时间为 ISO 8601 字符串；bbox 为相对页面宽高归一化的 [x0, y0, x1, y1]（0–1）。
 */

// ---------- 枚举 ----------

export const QUESTION_TYPES = ['单选题', '多选题', '填空题', '解答题'] as const
export type QuestionType = (typeof QUESTION_TYPES)[number]

/** 解析引擎，对应后端 DocParser 实现 */
export type ParserName = 'mineru_cloud' | 'mineru_local' | 'lite'

/** 流水线阶段，顺序即执行顺序 */
export const PARSE_STAGES = ['ocr', 'classify', 'segment', 'knowledge', 'difficulty', 'dedupe'] as const
export type ParseStage = (typeof PARSE_STAGES)[number]

export type JobStatus = 'uploading' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
export type StageStatus = 'pending' | 'running' | 'done' | 'skipped' | 'failed'

/** 置信度低于该值的题进入「待核对」 */
export const REVIEW_CONFIDENCE = 0.8

// ---------- 上传与任务 ----------

export interface ParseOptions {
  /** 图片 / 扫描件文字识别，对应 MinerU is_ocr */
  ocr: boolean
  /** 识别并关联答案解析 */
  answer: boolean
  /** 与题库查重 */
  dedupe: boolean
  /** 自动标注知识点；关闭时跳过 knowledge 阶段 */
  knowledge: boolean
}

export interface StageState {
  stage: ParseStage
  status: StageStatus
  /** 阶段完成后的摘要，如「识别 4 页」「9 道题」 */
  note?: string
}

// ---------- AI 用量与成本 ----------

/** AI 用量汇总；cost 为按当前单价估算的费用，未配置单价时为 null */
export interface UsageSummary {
  calls: number
  llmCalls: number
  errors: number
  promptTokens: number
  completionTokens: number
  /** 思考 tokens，已包含在 completionTokens 中 */
  reasoningTokens: number
  /** 缓存命中的输入 tokens，已包含在 promptTokens 中 */
  cachedTokens: number
  totalTokens: number
  /** MinerU 解析页数 */
  pages: number
  durationMs: number
  /** 存在服务端未返回、按字符数估算的用量 */
  estimated: boolean
  cost: number | null
  llmCost: number | null
  mineruCost: number | null
  /** false 表示部分调用缺少单价，cost 只包含已配置单价的部分 */
  priced: boolean
  unpricedModels: string[]
  currency: string
}

export interface UsageCall {
  id: number
  provider: 'llm' | 'mineru'
  /** classify 试卷分类 / segment 拆题 / parse 版面识别 */
  purpose: string
  model: string
  promptTokens: number
  completionTokens: number
  reasoningTokens: number
  cachedTokens: number
  pages: number
  durationMs: number
  estimated: boolean
  status: 'ok' | 'error'
  cost: number | null
  createdAt: string
}

export interface JobUsage {
  summary: UsageSummary
  calls: UsageCall[]
}

export interface UsageOverview {
  days: number
  /** 期间内完成的解析任务数 */
  jobs: number
  pages: number
  questions: number
  summary: UsageSummary
  costPerJob: number | null
  costPerPage: number | null
  costPerQuestion: number | null
  tokensPerJob: number | null
  daily: { date: string; jobs: number; totalTokens: number; pages: number; cost: number | null }[]
}

// ---------- AI 生成答案 ----------

export interface AnswerTask {
  status: 'queued' | 'running' | 'done' | 'failed'
  total: number
  done: number
  failed: number
  error: string | null
  /** 本次要生成答案的题 */
  questionIds: string[]
}

export interface GenerateAnswersOptions {
  /** 留空表示本卷所有缺少答案的题 */
  questionIds?: string[]
  /** 覆盖已有答案 */
  overwrite?: boolean
}

export interface ParseJob {
  id: string
  /** 批量上传时所属批次 */
  batchId: string | null
  /** 展示用文件名；多张图片时为「首个文件名 等 N 个文件」 */
  fileName: string
  fileCount: number
  /** 字节，多文件为总和 */
  fileSize: number
  fileType: 'pdf' | 'docx' | 'image'
  pageCount: number | null
  options: ParseOptions
  /** 实际使用的解析引擎（含降级后的结果），由后端路由决定 */
  parser: ParserName | null
  status: JobStatus
  /** 0–100 */
  progress: number
  stages: StageState[]
  meta: PaperMeta | null
  questionCount: number
  reviewCount: number
  /** 已保存到校本题库的题数 */
  savedCount: number
  error?: string
  /** 本任务的 AI 用量；尚未调用任何 AI 服务时为 null */
  usage: UsageSummary | null
  /** 最近一次 AI 生成答案任务；未发起过时为 null */
  answerTask: AnswerTask | null
  /** 已设为评测样本时为样本 id */
  evalSampleId?: string | null
  createdAt: string
}

/** SSE `GET /api/parse-jobs/{id}/events` 推送的消息体，即任务快照 */
export type ParseJobEvent = ParseJob

// ---------- 任务列表与批量解析 ----------

export interface JobListItem {
  id: string
  batchId: string | null
  fileName: string
  fileType: 'pdf' | 'docx' | 'image'
  status: JobStatus
  progress: number
  /** 进行中的阶段 */
  currentStage: ParseStage | null
  questionCount: number
  reviewCount: number
  savedCount: number
  error: string | null
  createdAt: string
}

export interface JobListPage {
  items: JobListItem[]
  total: number
  /** 排队中 + 解析中的任务数 */
  active: number
}

export interface JobListQuery {
  /** 如 ['queued', 'running'] */
  status?: JobStatus[]
  batchId?: string
  limit?: number
  offset?: number
}

export interface ParseBatch {
  id: string
  total: number
  /** 各状态的任务数 */
  counts: Partial<Record<JobStatus, number>>
  jobs: JobListItem[]
  createdAt: string
}

// ---------- 相似题 ----------

export interface SimilarQuestion {
  id: string
  /** bank 校本题库 / draft 其他试卷中尚未入库的题 */
  source: 'bank' | 'draft'
  type: string
  stem: string
  options: string[]
  answer: string | null
  /** 综合相似度 0–1 */
  score: number
  lexical: number
  /** 语义余弦；未启用向量模型时为 null */
  semantic: number | null
  /** 达到疑似重复阈值 */
  duplicate: boolean
  jobId: string | null
  fileName: string | null
  /** 出处 */
  origin: QuestionSource | null
  knowledgePoints: KnowledgePointRef[]
}

export interface SimilarQuery {
  limit?: number
  /** bank 仅校本题库；all 含其他试卷中尚未入库的题 */
  scope?: 'bank' | 'all'
  type?: string
}

// ---------- 知识树 ----------

export interface KnowledgeTree {
  id: string
  name: string
  subject: string
  stage: string
  textbook: string
  /** 内置示例 */
  builtin: boolean
  nodeCount: number
  createdAt: string
}

export interface KnowledgeTreeNode {
  id: string
  name: string
  aliases: string[]
  children: KnowledgeTreeNode[]
}

export interface KnowledgeTreeDetail extends KnowledgeTree {
  nodes: KnowledgeTreeNode[]
}

export interface KnowledgeTreeImport {
  format: 'json' | 'csv'
  content: string
  name?: string
  subject?: string
  stage?: string
  textbook?: string
}

export interface KnowledgeNodeHit {
  id: string
  name: string
  path: string
  score: number
}

// ---------- 评测 ----------

export interface EvalSample {
  id: string
  jobId: string
  fileName: string
  questionCount: number
  createdAt: string
}

/** 各项指标 0–1；样本中没有可统计的题时为 null */
export interface EvalMetrics {
  splitPrecision: number | null
  splitRecall: number | null
  typeAccuracy: number | null
  optionAccuracy: number | null
  answerAccuracy: number | null
  knowledgeTop3: number | null
  knowledgeRecall: number | null
  difficultyMae: number | null
  metaAccuracy: number | null
}

export interface DifficultyCalibration {
  a: number
  b: number
  n?: number
  maeBefore?: number
  maeAfter?: number
  source?: string
}

export interface EvalSampleResult {
  sampleId: string
  fileName: string
  jobId: string
  parser?: string | null
  error?: string
  metrics?: EvalMetrics
  unmatchedGold?: number[]
  extraPred?: number[]
  questions?: { no: number; predNo: number; similarity: number; issues: string[] }[]
}

export interface EvalRun {
  id: string
  status: 'queued' | 'running' | 'done' | 'failed'
  total: number
  done: number
  config: { parserChain?: string[]; llmModel?: string | null; mineruModel?: string | null; embeddingModel?: string | null }
  metrics: (EvalMetrics & { calibration: DifficultyCalibration | null; currentCalibration: DifficultyCalibration | null }) | null
  details: EvalSampleResult[]
  error: string | null
  createdAt: string
}

// ---------- 试卷分类 ----------

export interface PaperMeta {
  title: string        // 试卷名称，取自卷首标题
  stage: string        // 学段：小学 / 初中 / 高中
  subject: string      // 学科
  grade: string        // 年级
  paperType: string    // 试卷类型：期中考试 / 期末考试 / 月考 …
  region: string       // 地区
  schoolYear: string   // 学年
  textbook: string     // 教材版本
}

// ---------- 文档解析中间表示（IR） ----------

/** 解析引擎输出的最小单元，以 MinerU content_list.json 为蓝本 */
export interface Block {
  id: string
  page: number
  bbox: [number, number, number, number]
  type: 'text' | 'title' | 'equation' | 'image' | 'table'
  /** 文本内容；公式为 LaTeX，表格为 HTML */
  content: string
  imageUrl?: string
  score?: number
}

/** 题目出处；随试卷分类信息实时拼出 */
export interface QuestionSource {
  title: string
  fileName: string
  schoolYear: string
  region: string
  grade: string
  paperType: string
  subject: string
  no: number | null
  page: number | null
  /** 如「2026—2027 上 · 北京 · 海淀 · 高一期中考试《…》第 3 题」 */
  label: string
}

export interface SourceRegion {
  page: number
  bbox: [number, number, number, number]
}

// ---------- 草稿题 ----------

export interface KnowledgePointRef {
  id: string
  name: string
  /** 知识树中的完整路径；不在知识树中时为 null */
  path?: string | null
  /** 是否为知识树中的节点 */
  inTree?: boolean
}

export interface DraftQuestion {
  id: string
  jobId: string
  /** 卷内题号，合并 / 拆分后由后端重排 */
  no: number
  type: QuestionType
  score: number
  /** 起始页 */
  page: number
  /** 题干；行内公式用 $...$ 包裹 */
  stem: string
  /** 选项，不含「A．」前缀 */
  options: string[]
  answer: string | null
  analysis: string | null
  /** 答案来源：paper 原卷识别 / ai 大模型生成 / manual 人工修改 */
  answerSource: 'paper' | 'ai' | 'manual' | null
  /** AI 生成答案的提示，如「题目含图，AI 未看到图片，答案可能不准确」 */
  answerNote: string | null
  knowledgePoints: KnowledgePointRef[]
  /** 难度系数 0–1，越高越难，1 为最难（约等于 1 − 预估得分率） */
  coef: number
  /** baseline 按题位估算 / ai 大模型评估 / manual 老师调整 */
  difficultySource?: 'baseline' | 'ai' | 'manual' | null
  /** 识别置信度 0–1 */
  confidence: number
  /** 组成本题的 IR Block，用于合并 / 拆分与原图回溯 */
  blockIds: string[]
  regions: SourceRegion[]
  /** 题目内配图（几何图、函数图像等）的访问地址 */
  images: string[]
  /** 出处 */
  source: QuestionSource | null
  /** 查重命中的已有题目 id */
  duplicateOf: string | null
  status: 'draft' | 'saved'
}

/** PATCH 可修改的字段 */
export type DraftQuestionPatch = Partial<
  Pick<DraftQuestion, 'type' | 'score' | 'stem' | 'options' | 'answer' | 'analysis' | 'knowledgePoints' | 'coef'>
>

export interface SourceImage {
  page: number
  /** 整页图 */
  url: string
  /** 需要高亮的区域 */
  regions: SourceRegion[]
}

export interface RecentUpload {
  jobId: string
  fileName: string
  questionCount: number
  reviewCount: number
  savedCount: number
  createdAt: string
}

// ---------- 服务接口 ----------

export interface ParseApi {
  /**
   * 上传文件并创建解析任务（后端：逐个申请签名 → 直传对象存储 → POST /api/parse-jobs）。
   * 多张图片视为同一份试卷，按顺序作为连续页。
   */
  createJob(files: File[], options: ParseOptions, onUploadProgress?: (pct: number) => void): Promise<ParseJob>
  getJob(jobId: string): Promise<ParseJob>
  /** 订阅任务进度，返回取消订阅函数 */
  subscribe(jobId: string, onEvent: (job: ParseJobEvent) => void): () => void
  updateMeta(jobId: string, meta: PaperMeta): Promise<PaperMeta>
  listQuestions(jobId: string): Promise<DraftQuestion[]>
  updateQuestion(questionId: string, patch: DraftQuestionPatch): Promise<DraftQuestion>
  /** 与上一题合并，返回重排后的完整题目列表 */
  mergeWithPrevious(questionId: string): Promise<DraftQuestion[]>
  /** 按小问拆分，返回重排后的完整题目列表 */
  splitSubQuestions(questionId: string): Promise<DraftQuestion[]>
  getSource(questionId: string): Promise<SourceImage[]>
  /** 保存选中题目到校本题库 */
  commit(jobId: string, questionIds: string[]): Promise<{ savedCount: number }>
  listRecent(): Promise<RecentUpload[]>
  /** 任务的 AI 调用明细 */
  getUsage(jobId: string): Promise<JobUsage>
  /** 近 N 天用量与平均成本 */
  getUsageOverview(days: number): Promise<UsageOverview>
  /** 为缺少答案的题排队生成 AI 答案；进度通过 getJob 的 answerTask 获取 */
  generateAnswers(jobId: string, options?: GenerateAnswersOptions): Promise<AnswerTask>
  // ---- 知识树 ----
  listTrees(): Promise<KnowledgeTree[]>
  getTree(treeId: string): Promise<KnowledgeTreeDetail>
  importTree(req: KnowledgeTreeImport): Promise<KnowledgeTree>
  deleteTree(treeId: string): Promise<void>
  /** 知识点联想：按试卷自动选知识树，或指定知识树 */
  searchKnowledge(q: string, scope: { jobId?: string; treeId?: string }): Promise<KnowledgeNodeHit[]>
  // ---- 评测 ----
  /** 把核对后的结果设为评测样本（已是样本时覆盖） */
  markEvalSample(jobId: string): Promise<EvalSample>
  listEvalSamples(): Promise<EvalSample[]>
  deleteEvalSample(sampleId: string): Promise<void>
  createEvalRun(sampleIds?: string[]): Promise<EvalRun>
  listEvalRuns(): Promise<EvalRun[]>
  getEvalRun(runId: string): Promise<EvalRun>
  /** 采用评测拟合出的难度校准 */
  applyCalibration(runId: string): Promise<DifficultyCalibration>
  getCalibration(): Promise<DifficultyCalibration | null>
  clearCalibration(): Promise<void>
  /** 为尚未标注知识点的题补标（后台执行，进度见 stages 中 knowledge 阶段） */
  tagKnowledge(jobId: string): Promise<ParseJob>
  /** 批量上传：每一项是一份试卷的文件（1 个 PDF / Word，或多张图片），各自后台排队解析 */
  createBatch(papers: File[][], options: ParseOptions, onUploadProgress?: (pct: number) => void): Promise<ParseBatch>
  getBatch(batchId: string): Promise<ParseBatch>
  /** 任务列表，最新的在前 */
  listJobs(query?: JobListQuery): Promise<JobListPage>
  /** 取消排队中的任务 */
  cancelJob(jobId: string): Promise<ParseJob>
  /** 重新解析失败或已取消的任务 */
  retryJob(jobId: string): Promise<ParseJob>
  /** 与草稿题相似的题 */
  getSimilar(questionId: string, query?: SimilarQuery): Promise<SimilarQuestion[]>
  /** 按文本搜索相似题 */
  searchSimilar(text: string, query?: SimilarQuery): Promise<SimilarQuestion[]>
}
