import { query, request } from '../request'
import type { BankApi, BankQuestion, Page, PaperDetail, PaperPage } from './types'

export const httpBankApi: BankApi = {
  listQuestions: (p) => request<Page<BankQuestion>>('GET', `/api/bank/questions?${query({
    stage: p.stage, subject: p.subject, nodeId: p.nodeId, type: p.type, diff: p.diff, paperType: p.paperTypes?.join(','),
    year: p.year, q: p.q, paperId: p.paperId, sort: p.sort, limit: p.limit, offset: p.offset,
  })}`),
  knowledgeCounts: (treeId) => request<Record<string, number>>('GET', `/api/bank/knowledge-counts?${query({ treeId })}`),
  listPapers: (p) => request<PaperPage>('GET', `/api/papers?${query({
    stage: p.stage, grade: p.grade, subject: p.subject, paperType: p.paperType, q: p.q, limit: p.limit, offset: p.offset,
  })}`),
  getPaper: (id) => request<PaperDetail>('GET', `/api/papers/${encodeURIComponent(id)}`),
  removePaper: (id) => request<void>('DELETE', `/api/papers/${encodeURIComponent(id)}`),
}
