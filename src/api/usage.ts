import { query, request } from './request'
import type { UsageSummary } from './parse'

/** 成本分析中的一个分组（用途 / 模型 / 账号 / 学科）；cost 只含主货币 */
export interface CostBreakdown {
  key: string
  label: string
  provider: 'llm' | 'mineru' | null
  calls: number
  errors: number
  promptTokens: number
  completionTokens: number
  cachedTokens: number
  pages: number
  durationMs: number
  jobs: number
  cost: number
  /** 缺单价或其他货币、未计入金额的调用数 */
  unpricedCalls: number
}

export interface CostDay {
  /** 北京时间日期 YYYY-MM-DD */
  date: string
  calls: number
  jobs: number
  totalTokens: number
  pages: number
  llmCost: number
  mineruCost: number
  cost: number
}

export interface CostHour {
  hour: number
  calls: number
  cost: number
  /** 工作日该小时是否为计费来源的高峰价 */
  peak: boolean
}

export interface CostJob {
  id: string
  fileName: string
  status: string
  owner: string | null
  subject: string | null
  pages: number | null
  questions: number
  calls: number
  totalTokens: number
  cost: number
  createdAt: string
}

export interface CostAnalysis {
  start: string
  end: string
  days: number
  summary: UsageSummary & { costsByCurrency: Record<string, number> }
  previousCost: number | null
  previousStart: string
  previousEnd: string
  avgDailyCost: number | null
  projectedMonthlyCost: number | null
  jobs: number
  pages: number
  questions: number
  costPerJob: number | null
  costPerPage: number | null
  costPerQuestion: number | null
  errorCost: number
  cacheHitRate: number | null
  peakCostShare: number | null
  billingSource: string
  daily: CostDay[]
  hourly: CostHour[]
  byPurpose: CostBreakdown[]
  byModel: CostBreakdown[]
  byUser: CostBreakdown[]
  bySubject: CostBreakdown[]
  topJobs: CostJob[]
}

/** 单价阶梯（原样返回的字典，键为下划线命名）；每百万 tokens，MinerU 为每页 */
export interface PriceTier {
  max_input?: number | null
  input?: number
  output?: number
  input_offpeak?: number | null
  output_offpeak?: number | null
  cached_input?: number | null
  mode?: string | null
  per_page?: number
}

export interface ModelPrice {
  id: number
  provider: 'llm' | 'mineru'
  model: string
  source: string
  currency: string
  tiers: PriceTier[]
  cacheRatio: number | null
  peak: { ranges: [number, number][]; weekdays_only?: boolean } | null
  notes: string | null
  /** 用于计算成本（手动配置或当前调用平台），其余为参考 */
  billing: boolean
  fetchedAt: string
  checkedAt: string
}

export const usageApi = {
  /** start、end 为北京时间日期（含首尾） */
  analysis: (start: string, end: string) => request<CostAnalysis>('GET', `/api/usage/analysis?${query({ start, end })}`),
  prices: () => request<ModelPrice[]>('GET', '/api/usage/prices'),
}
