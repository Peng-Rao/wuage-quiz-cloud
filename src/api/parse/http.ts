import type {
  AnswerTask, DraftQuestion, JobListPage, JobUsage, ParseApi, ParseBatch, ParseJob, PaperMeta, RecentUpload,
  SimilarQuestion, SourceImage, UsageOverview,
} from './types'

/** 按 docs/ai-parse-api.md 对接后端；P0 阶段未启用 */

const BASE = import.meta.env.VITE_API_BASE ?? ''

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'include',
  })
  if (!res.ok) {
    const err = await res.json().catch(() => null)
    throw new Error(err?.message ?? `请求失败（HTTP ${res.status}）`)
  }
  return res.json() as Promise<T>
}

function putWithProgress(url: string, file: File, onProgress?: (pct: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    xhr.upload.onprogress = e => e.lengthComputable && onProgress?.(Math.round((e.loaded / e.total) * 100))
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`上传失败（HTTP ${xhr.status}）`)))
    xhr.onerror = () => reject(new Error('上传失败，请检查网络'))
    xhr.send(file)
  })
}

/** 逐个申请签名并直传，按字节数汇总进度 */
async function uploadAll(files: File[], onProgress?: (pct: number) => void): Promise<string[]> {
  const total = files.reduce((a, f) => a + f.size, 0) || 1
  let done = 0
  const keys: string[] = []
  for (const file of files) {
    const { uploadUrl, fileKey } = await request<{ uploadUrl: string; fileKey: string }>('POST', '/api/uploads', {
      fileName: file.name,
      fileSize: file.size,
      contentType: file.type,
    })
    await putWithProgress(uploadUrl, file, pct => onProgress?.(Math.round(((done + (file.size * pct) / 100) / total) * 100)))
    done += file.size
    keys.push(fileKey)
  }
  return keys
}

export const httpParseApi: ParseApi = {
  async createJob(files, options, onUploadProgress) {
    const fileKeys = await uploadAll(files, onUploadProgress)
    return request<ParseJob>('POST', '/api/parse-jobs', { fileKeys, fileNames: files.map(f => f.name), options })
  },

  async createBatch(papers, options, onUploadProgress) {
    const keys = await uploadAll(papers.flat(), onUploadProgress)
    let i = 0
    const items = papers.map(files => ({ fileKeys: files.map(() => keys[i++]), fileNames: files.map(f => f.name) }))
    return request<ParseBatch>('POST', '/api/parse-batches', { items, options })
  },

  getBatch: id => request<ParseBatch>('GET', `/api/parse-batches/${id}`),

  listJobs(query = {}) {
    const q = new URLSearchParams()
    if (query.status?.length) q.set('status', query.status.join(','))
    if (query.batchId) q.set('batchId', query.batchId)
    if (query.limit) q.set('limit', String(query.limit))
    if (query.offset) q.set('offset', String(query.offset))
    return request<JobListPage>('GET', `/api/parse-jobs?${q}`)
  },

  cancelJob: id => request<ParseJob>('POST', `/api/parse-jobs/${id}/cancel`),
  tagKnowledge: id => request<ParseJob>('POST', `/api/parse-jobs/${id}/tag-knowledge`),
  retryJob: id => request<ParseJob>('POST', `/api/parse-jobs/${id}/retry`),

  getSimilar(questionId, query = {}) {
    const q = new URLSearchParams({ limit: String(query.limit ?? 5), scope: query.scope ?? 'all' })
    return request<SimilarQuestion[]>('GET', `/api/draft-questions/${questionId}/similar?${q}`)
  },

  searchSimilar: (text, query = {}) =>
    request<SimilarQuestion[]>('POST', '/api/similar/search', { text, limit: query.limit ?? 10, scope: query.scope ?? 'bank', type: query.type }),

  getJob: jobId => request('GET', `/api/parse-jobs/${jobId}`),

  subscribe(jobId, onEvent) {
    const es = new EventSource(`${BASE}/api/parse-jobs/${jobId}/events`, { withCredentials: true })
    es.onmessage = e => {
      const job = JSON.parse(e.data) as ParseJob
      onEvent(job)
      if (['done', 'failed', 'cancelled'].includes(job.status)) es.close()
    }
    return () => es.close()
  },

  updateMeta: (jobId, meta) => request<PaperMeta>('PUT', `/api/parse-jobs/${jobId}/meta`, meta),
  listQuestions: jobId => request<DraftQuestion[]>('GET', `/api/parse-jobs/${jobId}/questions`),
  updateQuestion: (id, patch) => request<DraftQuestion>('PATCH', `/api/draft-questions/${id}`, patch),
  mergeWithPrevious: id => request<DraftQuestion[]>('POST', `/api/draft-questions/${id}/merge-previous`),
  splitSubQuestions: id => request<DraftQuestion[]>('POST', `/api/draft-questions/${id}/split`),
  getSource: id => request<SourceImage[]>('GET', `/api/draft-questions/${id}/source`),
  commit: (jobId, questionIds) => request('POST', `/api/parse-jobs/${jobId}/commit`, { questionIds }),
  listRecent: () => request<RecentUpload[]>('GET', '/api/parse-jobs?recent=1'),
  getUsage: jobId => request<JobUsage>('GET', `/api/parse-jobs/${jobId}/usage`),
  getUsageOverview: days => request<UsageOverview>('GET', `/api/usage/summary?days=${days}`),
  generateAnswers: (jobId, options = {}) =>
    request<AnswerTask>('POST', `/api/parse-jobs/${jobId}/generate-answers`, options),
}
