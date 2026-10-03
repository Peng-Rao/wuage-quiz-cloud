import type {
  DifficultyCalibration, EvalRun, EvalSample, KnowledgeNodeHit, KnowledgeTree, KnowledgeTreeDetail,
  AnswerTask, CommitResult, DraftQuestion, JobListPage, JobUsage, ParseApi, ParseBatch, ParseJob, PaperMeta, RecentUpload,
  SimilarQuestion, SourceImage, UsageOverview,
} from './types'
import { BASE, request } from '../request'
import { useAppStore } from '@/stores/app'

/** 按 docs/ai-parse-api.md 对接后端 */

/** 上传到本服务（本地存储）或对象存储的预签名地址；headers 为签名包含的请求头，须原样携带 */
function putWithProgress(url: string, file: File, headers: Record<string, string>, onProgress?: (pct: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const own = url.startsWith('/')
    xhr.open('PUT', own ? BASE + url : url)
    // 登录 Cookie 只发给本服务；对象存储凭签名鉴权，携带 Cookie 反而需要更严格的跨域配置
    xhr.withCredentials = own
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v)
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
    const { uploadUrl, uploadHeaders, fileKey } = await request<{ uploadUrl: string; uploadHeaders?: Record<string, string>; fileKey: string }>('POST', '/api/uploads', {
      fileName: file.name,
      fileSize: file.size,
      contentType: file.type,
    })
    await putWithProgress(uploadUrl, file, uploadHeaders ?? {}, pct => onProgress?.(Math.round(((done + (file.size * pct) / 100) / total) * 100)))
    done += file.size
    // 小文件或极快上传可能没有 progress 事件；服务端确认接收后同步完成进度。
    onProgress?.(Math.round((done / total) * 100))
    keys.push(fileKey)
  }
  return keys
}

export const httpParseApi: ParseApi = {
  async createJob(files, options, onUploadProgress) {
    const fileKeys = await uploadAll(files, onUploadProgress)
    return request<ParseJob>('POST', '/api/parse-jobs', { fileKeys, fileNames: files.map(f => f.name), options: { ...options, subject: useAppStore().subject } })
  },

  async createBatch(papers, options, onUploadProgress) {
    const keys = await uploadAll(papers.flat(), onUploadProgress)
    let i = 0
    const items = papers.map(files => ({ fileKeys: files.map(() => keys[i++]), fileNames: files.map(f => f.name) }))
    return request<ParseBatch>('POST', '/api/parse-batches', { items, options: { ...options, subject: useAppStore().subject } })
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

  listTrees: () => request<KnowledgeTree[]>('GET', '/api/knowledge-trees'),
  getTree: id => request<KnowledgeTreeDetail>('GET', `/api/knowledge-trees/${id}`),
  importTree: req => request<KnowledgeTree>('POST', '/api/knowledge-trees/import', req),
  deleteTree: id => request<void>('DELETE', `/api/knowledge-trees/${id}`),
  searchKnowledge(q, scope) {
    const p = new URLSearchParams({ q })
    if (scope.jobId) p.set('jobId', scope.jobId)
    if (scope.treeId) p.set('treeId', scope.treeId)
    return request<KnowledgeNodeHit[]>('GET', `/api/knowledge/search?${p}`)
  },

  markEvalSample: jobId => request<EvalSample>('POST', `/api/parse-jobs/${jobId}/eval-sample`),
  listEvalSamples: () => request<EvalSample[]>('GET', '/api/eval-samples'),
  deleteEvalSample: id => request<void>('DELETE', `/api/eval-samples/${id}`),
  createEvalRun: sampleIds => request<EvalRun>('POST', '/api/eval-runs', { sampleIds }),
  listEvalRuns: () => request<EvalRun[]>('GET', '/api/eval-runs'),
  getEvalRun: id => request<EvalRun>('GET', `/api/eval-runs/${id}`),
  applyCalibration: id => request<DifficultyCalibration>('POST', `/api/eval-runs/${id}/apply-calibration`),
  getCalibration: () => request<DifficultyCalibration | null>('GET', '/api/difficulty-calibration'),
  clearCalibration: () => request<void>('DELETE', '/api/difficulty-calibration'),
  retryJob: id => request<ParseJob>('POST', `/api/parse-jobs/${id}/retry`),

  getSimilar(questionId, query = {}) {
    const q = new URLSearchParams({ limit: String(query.limit ?? 5), scope: query.scope ?? 'all' })
    return request<SimilarQuestion[]>('GET', `/api/draft-questions/${questionId}/similar?${q}`)
  },

  searchSimilar: (text, query = {}) =>
    request<SimilarQuestion[]>('POST', '/api/similar/search', { text, limit: query.limit ?? 10, scope: query.scope ?? 'bank', type: query.type }),

  getJob: jobId => request('GET', `/api/parse-jobs/${jobId}`),

  async getPdf(jobId, signal) {
    const res = await fetch(`${BASE}/api/parse-jobs/${encodeURIComponent(jobId)}/pdf`, {
      credentials: 'include', signal,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => null)
      if (res.status === 401 && window.location.pathname !== '/login') window.location.replace('/login')
      throw new Error(err?.message ?? `PDF 加载失败（HTTP ${res.status}）`)
    }
    return res.blob()
  },

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
  commit: (jobId, questionIds, force = false) =>
    request<CommitResult>('POST', `/api/parse-jobs/${jobId}/commit`, { questionIds, force }),
  listRecent: () => request<RecentUpload[]>('GET', '/api/parse-jobs?recent=1'),
  getUsage: jobId => request<JobUsage>('GET', `/api/parse-jobs/${jobId}/usage`),
  getUsageOverview: days => request<UsageOverview>('GET', `/api/usage/summary?days=${days}`),
  generateAnswers: (jobId, options = {}) =>
    request<AnswerTask>('POST', `/api/parse-jobs/${jobId}/generate-answers`, options),
}
