/**
 * 校本题库选题与试卷库接口契约，与后端 server/app/schemas.py 对应。
 */
import type { AnswerTask, KnowledgePointRef, PaperMeta, QuestionSource, QuestionType } from '../parse/types'

/** 已入库的题目 */
export interface BankQuestion {
  ownerId?: string | null
  reviewedBy?: string | null
  reviewedAt?: string | null
  id: string
  type: QuestionType
  /** 原卷分值 */
  score: number
  stem: string
  /** 阅读材料（英语阅读 / 完形填空原文、语文选文等） */
  material?: string | null
  /** 选项，不含「A．」前缀 */
  options: string[]
  answer: string | null
  analysis: string | null
  answerSource: 'paper' | 'ai' | 'manual' | null
  /** AI 生成答案的提示，如「题目含图，AI 未看到图片，答案可能不准确」 */
  answerNote?: string | null
  knowledgePoints: KnowledgePointRef[]
  /** 难度系数 0–1，越高越难 */
  coef: number
  images: string[]
  source: QuestionSource | null
  /** 所属试卷（试卷库中的 id） */
  paperId: string
  createdAt: string
}

/** default 综合（按试卷、题号）/ latest 最新入库 / easy 由易到难 / hard 由难到易 */
export type BankSort = 'default' | 'latest' | 'easy' | 'hard'

export interface BankQuery {
  stage?: string
  subject?: string
  /** 知识点所在的知识树（mock 按路径匹配时需要） */
  treeId?: string
  /** 知识点节点，含所有下级知识点；多个时含任一即可 */
  nodeIds?: string[]
  /** 教材章或节（见 chapters） */
  chapterId?: string
  type?: QuestionType
  diff?: '容易' | '适中' | '较难'
  /** 场景：试卷类型或名称含任一关键词，如 ['期中', '期末'] */
  paperTypes?: string[]
  /** '2026'；'<2024' 表示更早 */
  year?: string
  /** 省级地区，如「北京」 */
  region?: string
  grade?: string
  term?: '上' | '下'
  q?: string
  paperId?: string
  sort?: BankSort
  limit?: number
  offset?: number
}

export interface Page<T> {
  items: T[]
  total: number
}

export interface FacetCount {
  name: string
  count: number
}

/** 试卷库中的一份试卷：同一份原卷入库的题；id 为来源解析任务 id */
export interface PaperSummary {
  id: string
  title: string
  meta: PaperMeta
  fileName: string
  /** 已入库题数 */
  questionCount: number
  /** 原卷拆出的题数，部分入库时大于 questionCount */
  sourceQuestionCount: number
  totalScore: number
  typeCounts: Partial<Record<QuestionType, number>>
  /** 按分值加权的平均难度系数 */
  avgCoef: number | null
  /** 最近一次入库时间 */
  updatedAt: string
}

export interface PaperFacets {
  stages: FacetCount[]
  grades: FacetCount[]
  subjects: FacetCount[]
  paperTypes: FacetCount[]
  /** 教材版本 */
  textbooks: FacetCount[]
}

/** 选题「更多」筛选的可选值 */
export interface QuestionFacets {
  regions: FacetCount[]
  grades: FacetCount[]
  years: FacetCount[]
}

export interface ChapterSection {
  id: string
  name: string
  /** 对应的知识点名称：题目知识点（含各级上级）属于其中之一即归入本节 */
  knowledge: string[]
}

export interface ChapterItem {
  id: string
  name: string
  sections: ChapterSection[]
}

/** 一册教材，如「必修 第一册」 */
export interface TextbookBook {
  id: string
  name: string
  grade: string
  /** 平台标注的「新教材」「旧教材」；新教材目录未上线时为旧教材 */
  edition: string
  chapters: ChapterItem[]
}

/** 教材版本，如「人教A版」；同一学科多个版本时第一个为默认 */
export interface TextbookVersion {
  name: string
  /** 适用地区，如「厦门」 */
  region: string
  books: TextbookBook[]
}

export interface PaperPage extends Page<PaperSummary> {
  /** 各维度在其余筛选条件下的试卷数；没有分类的记为「未分类」 */
  facets: PaperFacets
}

export interface PaperDetail extends PaperSummary {
  /** 按原卷题号排列 */
  questions: BankQuestion[]
}

export interface PaperQuery {
  stage?: string
  grade?: string
  subject?: string
  paperType?: string
  /** 教材版本 */
  textbook?: string
  /** 试卷分类（同步教学、阶段测试等）：试卷类型或名称含任一关键词 */
  category?: string[]
  q?: string
  limit?: number
  offset?: number
}

/** AI 组卷（Demo） */
export interface ComposeMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ComposeRequest {
  stage: string
  subject: string
  /** 老师当前的设置；对话中要求改变时以返回结果为准 */
  total: number
  difficulty: number
  /** 完整对话，最后一条为本次的要求 */
  messages: ComposeMessage[]
}

export interface ComposeSection {
  type: QuestionType
  score: number
  items: { question: BankQuestion; score: number }[]
}

export interface ComposeResult {
  /** 给老师的组卷说明 */
  reply: string
  title: string
  total: number
  /** 目标平均难度 */
  difficulty: number
  /** 实际平均难度（按分值加权） */
  actualDifficulty: number
  sections: ComposeSection[]
  /** 重点知识点及选入的题数 */
  focus: { name: string; path: string | null; weight: number; count: number }[]
  /** 题库不足等提示 */
  gaps: string[]
  /** 需求是否由大模型理解；否则为按关键词匹配 */
  ai: boolean
}

export interface BankApi {
  listQuestions(query: BankQuery): Promise<Page<BankQuestion>>
  /** 选题「更多」筛选的可选值 */
  questionFacets(stage: string, subject: string): Promise<QuestionFacets>
  /** 学段学科的教材版本与章节目录 */
  chapters(stage: string, subject: string): Promise<TextbookVersion[]>
  /** 某册教材各章、节的入库题数，没有题的不返回 */
  chapterCounts(bookId: string): Promise<Record<string, number>>
  /** 知识树各节点（含下级）的入库题数，没有题的节点不返回 */
  knowledgeCounts(treeId: string): Promise<Record<string, number>>
  listPapers(query: PaperQuery): Promise<PaperPage>
  getPaper(paperId: string): Promise<PaperDetail>
  /** 移出试卷库：删除该卷已入库的题，原卷草稿题恢复为未保存，可在试卷解析中重新保存 */
  removePaper(paperId: string): Promise<void>
  /** 为本卷已入库、缺少答案的题（或其中指定的题）生成 AI 答案，直接写入题库；生成后需重新审核（仅管理员、组长） */
  generateAnswers(paperId: string, questionIds?: string[]): Promise<AnswerTask>
  /** 本卷最近一次 AI 生成答案任务的进度，没有时为 null */
  answerTask(paperId: string): Promise<AnswerTask | null>
  /** AI 组卷：按学生情况与要求从题库选题并赋分；多轮修改时传入完整对话，整份试卷重新生成 */
  compose(req: ComposeRequest): Promise<ComposeResult>
}
