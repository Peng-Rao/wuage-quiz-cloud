/**
 * 校本题库选题与试卷库接口契约，与后端 server/app/schemas.py 对应。
 */
import type { KnowledgePointRef, PaperMeta, QuestionSource, QuestionType } from '../parse/types'

/** 已入库的题目 */
export interface BankQuestion {
  id: string
  type: QuestionType
  /** 原卷分值 */
  score: number
  stem: string
  /** 选项，不含「A．」前缀 */
  options: string[]
  answer: string | null
  analysis: string | null
  answerSource: 'paper' | 'ai' | 'manual' | null
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
  /** 知识点节点，含所有下级知识点 */
  nodeId?: string
  type?: QuestionType
  diff?: '容易' | '适中' | '较难'
  /** 试卷类型关键词，含任一即可，如 ['期中', '期末'] */
  paperTypes?: string[]
  /** '2026'；'<2024' 表示更早 */
  year?: string
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
  q?: string
  limit?: number
  offset?: number
}

export interface BankApi {
  listQuestions(query: BankQuery): Promise<Page<BankQuestion>>
  /** 知识树各节点（含下级）的入库题数，没有题的节点不返回 */
  knowledgeCounts(treeId: string): Promise<Record<string, number>>
  listPapers(query: PaperQuery): Promise<PaperPage>
  getPaper(paperId: string): Promise<PaperDetail>
  /** 移出试卷库：删除该卷已入库的题，原卷草稿题恢复为未保存，可在试卷解析中重新保存 */
  removePaper(paperId: string): Promise<void>
}
