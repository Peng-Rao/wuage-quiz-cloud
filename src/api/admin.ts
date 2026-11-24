/** 管理后台接口（/api/admin/*）：独立的邮箱账号与会话 */
import { query, request } from './request'
import type { CostAnalysis, ModelPrice, PriceTier } from './usage'
import type { PaperMeta, UsageSummary } from './parse'
import type { User } from '@/stores/auth'

export type Permission = 'exams' | 'users' | 'keys' | 'ai'
export const PERMISSION_LABELS: Record<Permission, string> = {
  exams: '试卷管理',
  users: '用户管理',
  keys: '密钥配置',
  ai: '价格与用量',
}

export interface ConsoleAccount {
  id: string
  email: string
  displayName: string
  isRoot: boolean
  permissions: Permission[]
  active: boolean
  createdAt: string
  lastLoginAt: string | null
  lastSeenAt: string | null
}

export interface Overview {
  users: { total: number; active: number; online: number }
  exams: { total: number; byStatus: Record<string, number>; papers: number; questions: number }
  ai: { monthCost: number | null; currency: string; monthCalls: number; monthTokens: number; monthPages: number }
  services: { llm: boolean; vision: boolean; mineru: boolean; embedding: boolean; worker: Record<string, unknown> }
}

export interface ExamItem {
  id: string
  fileName: string
  fileType: 'pdf' | 'docx' | 'image'
  fileCount: number
  fileSize: number
  title: string
  subject: string | null
  status: 'uploading' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  progress: number
  pageCount: number | null
  parser: string | null
  owner: { id: string; username: string; displayName: string } | null
  questionCount: number
  savedCount: number
  bankCount: number
  calls: number
  cost: number | null
  currency: string
  error: string | null
  createdAt: string
  updatedAt: string
}
export interface ExamPage { items: ExamItem[]; total: number; byStatus: Record<string, number> }
export interface ExamQuestion {
  id: string; no: number; type: string; score: number; page: number; stem: string
  status: string; confidence: number; hasAnswer: boolean; knowledge: string[]
}
export interface ExamDetail extends ExamItem {
  meta: PaperMeta | null
  options: Record<string, unknown>
  stages: { stage: string; status: string; note: string | null }[]
  warnings: unknown[]
  files: { index: number; name: string; key: string }[]
  usage: UsageSummary | null
  questions: ExamQuestion[]
}

export interface ConsoleUser extends User { isOnline: boolean; lastSeenAt: string | null; sessions: number; uploads: number }

export interface SettingField {
  key: string
  group: string
  label: string
  kind: 'text' | 'secret' | 'select' | 'json' | 'number'
  help: string
  options: string[]
  value: unknown
  configured: boolean
  source: 'console' | 'env' | 'default'
  updatedBy: string | null
  updatedAt: string | null
}

export interface PriceStatus {
  billingSource: string
  costSources: string[]
  sources: string[]
  refreshHours: number
  checkedAt: string | null
  models: string[]
}
export interface PriceWrite {
  provider: 'llm' | 'mineru'
  model: string
  currency: 'CNY' | 'USD'
  tiers?: PriceTier[]
  perPage?: number | null
  cacheRatio?: number | null
  peak?: { ranges: [number, number][]; weekdays_only: boolean } | null
  notes?: string
}

export interface AuditItem { id: number; actor: string; action: string; target: string | null; detail: Record<string, unknown> | null; createdAt: string }

const A = '/api/admin'
export const adminApi = {
  login: (email: string, password: string) => request<ConsoleAccount>('POST', `${A}/auth/login`, { email, password }),
  me: () => request<ConsoleAccount>('GET', `${A}/auth/me`),
  logout: () => request<void>('POST', `${A}/auth/logout`),
  changePassword: (current: string, next: string) => request<void>('PUT', `${A}/auth/password`, { current, new: next }),
  overview: () => request<Overview>('GET', `${A}/overview`),

  accounts: () => request<ConsoleAccount[]>('GET', `${A}/accounts`),
  createAccount: (body: { email: string; displayName: string; password: string; permissions: Permission[]; active: boolean }) =>
    request<ConsoleAccount>('POST', `${A}/accounts`, body),
  updateAccount: (id: string, body: { displayName: string; permissions: Permission[]; active: boolean; password?: string }) =>
    request<ConsoleAccount>('PUT', `${A}/accounts/${id}`, body),
  deleteAccount: (id: string) => request<void>('DELETE', `${A}/accounts/${id}`),
  audit: (params: { limit?: number; offset?: number; action?: string }) =>
    request<{ items: AuditItem[]; total: number }>('GET', `${A}/audit?${query(params)}`),

  exams: (params: { q?: string; status?: string; subject?: string; limit?: number; offset?: number }) =>
    request<ExamPage>('GET', `${A}/exams?${query(params)}`),
  exam: (id: string) => request<ExamDetail>('GET', `${A}/exams/${id}`),
  examFileUrl: (id: string, index: number) => `${A}/exams/${id}/files/${index}`,
  updateExamMeta: (id: string, meta: PaperMeta) => request<PaperMeta>('PUT', `${A}/exams/${id}/meta`, meta),
  retryExam: (id: string) => request<ExamItem>('POST', `${A}/exams/${id}/retry`),
  cancelExam: (id: string) => request<ExamItem>('POST', `${A}/exams/${id}/cancel`),
  unpublishExam: (id: string) => request<{ removed: number }>('POST', `${A}/exams/${id}/unpublish`),
  deleteExam: (id: string) => request<void>('DELETE', `${A}/exams/${id}`),

  users: () => request<ConsoleUser[]>('GET', `${A}/users`),
  saveUser: (id: string, body: Record<string, unknown>) =>
    request<User>(id ? 'PUT' : 'POST', id ? `${A}/users/${id}` : `${A}/users`, body),
  signOutUser: (id: string) => request<void>('POST', `${A}/users/${id}/sign-out`),
  deleteUser: (id: string) => request<void>('DELETE', `${A}/users/${id}`),

  settings: () => request<SettingField[]>('GET', `${A}/settings`),
  saveSettings: (values: Record<string, unknown>) => request<SettingField[]>('PUT', `${A}/settings`, { values }),
  testSettings: (target: 'llm' | 'vision' | 'embedding') =>
    request<{ ok: boolean; status?: number; latencyMs?: number; model?: string; message: string }>('POST', `${A}/settings/test`, { target }),

  usage: {
    analysis: (start: string, end: string) => request<CostAnalysis>('GET', `${A}/usage/analysis?${query({ start, end })}`),
    prices: () => request<ModelPrice[]>('GET', `${A}/prices`),
  },
  prices: (history = false) => request<ModelPrice[]>('GET', `${A}/prices?${query({ history: history ? 'true' : undefined })}`),
  priceStatus: () => request<PriceStatus>('GET', `${A}/prices/status`),
  refreshPrices: () => request<{ lines: string[] }>('POST', `${A}/prices/refresh`),
  setPrice: (body: PriceWrite) => request<ModelPrice>('POST', `${A}/prices`, body),
  clearManualPrice: (provider: string, model: string) => request<void>('DELETE', `${A}/prices/manual?${query({ provider, model })}`),
  recompute: (body: { since?: string | null; all?: boolean }) => request<{ priced: number; missing: number }>('POST', `${A}/prices/recompute`, body),
}
