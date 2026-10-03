import { query, request } from '../request'
import type { AnswerTask } from '../parse/types'
import type {
  BankApi, BankQuestion, ComposeResult, Page, PaperDetail, PaperPage, QuestionFacets, TextbookVersion,
} from './types'

export const httpBankApi: BankApi = {
  listQuestions: (p) => request<Page<BankQuestion>>('GET', `/api/bank/questions?${query({
    stage: p.stage, subject: p.subject, nodeId: p.nodeIds?.join(','), chapterId: p.chapterId, type: p.type, diff: p.diff,
    paperType: p.paperTypes?.join(','), year: p.year, region: p.region, grade: p.grade, term: p.term, q: p.q,
    paperId: p.paperId, sort: p.sort, limit: p.limit, offset: p.offset,
  })}`),
  questionFacets: (stage, subject) => request<QuestionFacets>('GET', `/api/bank/facets?${query({ stage, subject })}`),
  chapters: (stage, subject) => request<TextbookVersion[]>('GET', `/api/chapters?${query({ stage, subject })}`),
  chapterCounts: (bookId) => request<Record<string, number>>('GET', `/api/bank/chapter-counts?${query({ bookId })}`),
  knowledgeCounts: (treeId) => request<Record<string, number>>('GET', `/api/bank/knowledge-counts?${query({ treeId })}`),
  listPapers: (p) => request<PaperPage>('GET', `/api/papers?${query({
    stage: p.stage, grade: p.grade, subject: p.subject, paperType: p.paperType, school: p.school,
    category: p.category?.join(','), q: p.q, limit: p.limit, offset: p.offset,
  })}`),
  getPaper: (id) => request<PaperDetail>('GET', `/api/papers/${encodeURIComponent(id)}`),
  updateQuestion: (id, patch) => request<BankQuestion>('PATCH', `/api/bank/questions/${encodeURIComponent(id)}`, patch),
  removePaper: (id) => request<void>('DELETE', `/api/papers/${encodeURIComponent(id)}`),
  generateAnswers: (id, questionIds) =>
    request<AnswerTask>('POST', `/api/papers/${encodeURIComponent(id)}/generate-answers`, { questionIds }),
  answerTask: (id) => request<AnswerTask | null>('GET', `/api/papers/${encodeURIComponent(id)}/answer-task`),
  compose: (req) => request<ComposeResult>('POST', '/api/compose', req),
}
