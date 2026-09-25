import type { DraftQuestion, ParseApi, ParseJob, PaperMeta, RecentUpload, SourceImage } from './types'

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

export const httpParseApi: ParseApi = {
  async createJob(files, options, onUploadProgress) {
    const total = files.reduce((a, f) => a + f.size, 0)
    let done = 0
    const fileKeys: string[] = []
    for (const file of files) {
      const { uploadUrl, fileKey } = await request<{ uploadUrl: string; fileKey: string }>('POST', '/api/uploads', {
        fileName: file.name,
        fileSize: file.size,
        contentType: file.type,
      })
      await putWithProgress(uploadUrl, file, pct => onUploadProgress?.(Math.round(((done + (file.size * pct) / 100) / total) * 100)))
      done += file.size
      fileKeys.push(fileKey)
    }
    return request<ParseJob>('POST', '/api/parse-jobs', { fileKeys, fileNames: files.map(f => f.name), options })
  },

  getJob: jobId => request('GET', `/api/parse-jobs/${jobId}`),

  subscribe(jobId, onEvent) {
    const es = new EventSource(`${BASE}/api/parse-jobs/${jobId}/events`, { withCredentials: true })
    es.onmessage = e => {
      const job = JSON.parse(e.data) as ParseJob
      onEvent(job)
      if (job.status === 'done' || job.status === 'failed') es.close()
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
}
