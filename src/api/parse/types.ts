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
export const PARSE_STAGES = ['ocr', 'classify', 'segment', 'knowledge', 'difficulty'] as const
export type ParseStage = (typeof PARSE_STAGES)[number]

export type JobStatus = 'uploading' | 'queued' | 'running' | 'done' | 'failed'
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

export interface ParseJob {
  id: string
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
  createdAt: string
}

/** SSE `GET /api/parse-jobs/{id}/events` 推送的消息体，即任务快照 */
export type ParseJobEvent = ParseJob

// ---------- 试卷分类 ----------

export interface PaperMeta {
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

export interface SourceRegion {
  page: number
  bbox: [number, number, number, number]
}

// ---------- 草稿题 ----------

export interface KnowledgePointRef {
  id: string
  name: string
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
  knowledgePoints: KnowledgePointRef[]
  /** 难度系数，即预估得分率 0–1，越低越难 */
  coef: number
  /** 识别置信度 0–1 */
  confidence: number
  /** 组成本题的 IR Block，用于合并 / 拆分与原图回溯 */
  blockIds: string[]
  regions: SourceRegion[]
  /** 题目内配图（几何图、函数图像等）的访问地址 */
  images: string[]
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
}
