import {
  PARSE_STAGES,
  REVIEW_CONFIDENCE,
  type DraftQuestion,
  type ParseApi,
  type UsageCall,
  type UsageOverview,
  type ParseJob,
  type ParseJobEvent,
  type ParseOptions,
  type ParseStage,
  type RecentUpload,
  type StageState,
} from './types'
import {
  MOCK_META, MOCK_PAGE_COUNT, MOCK_RECENT, buildMockQuestions, buildMockUsage, mockAiAnswer, mockPageImage,
  summarizeMockUsage,
} from './mockData'

/**
 * 内存版解析服务：模拟上传、分阶段进度推送和草稿题编辑。
 * 行为与 docs/ai-parse-api.md 保持一致，后端就绪后切换到 http 实现。
 */

export const MAX_FILE_SIZE = 50 * 1024 * 1024

const TICK_MS = 90
const STEP = 3

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))
const clone = <T>(v: T): T => structuredClone(v)

const jobs = new Map<string, ParseJob>()
const questions = new Map<string, DraftQuestion[]>()
const listeners = new Map<string, Set<(job: ParseJobEvent) => void>>()
const recent: RecentUpload[] = [...MOCK_RECENT]
const usage = new Map<string, UsageCall[]>()

const STAGE_NOTES: Record<ParseStage, (job: ParseJob, qs: DraftQuestion[]) => string> = {
  ocr: job => `识别 ${job.pageCount} 页`,
  classify: () => `${MOCK_META.stage} · ${MOCK_META.subject} · ${MOCK_META.paperType.replace('考试', '')}`,
  segment: (_, qs) => `${qs.length} 道题`,
  knowledge: (_, qs) => `${new Set(qs.flatMap(q => q.knowledgePoints.map(k => k.id))).size} 个知识点`,
  difficulty: () => '预估得分率',
}

function fileTypeOf(file: File): ParseJob['fileType'] {
  if (/\.docx$/i.test(file.name)) return 'docx'
  if (/\.(png|jpe?g)$/i.test(file.name) || file.type.startsWith('image/')) return 'image'
  return 'pdf'
}

/** 与后端校验规则一致：单份试卷只能是 1 个 PDF / Word，或若干张图片 */
function validate(files: File[]) {
  if (!files.length) throw new Error('请选择文件')
  const types = files.map(fileTypeOf)
  if (files.length > 1 && types.some(t => t !== 'image')) throw new Error('多个文件仅支持图片（拍照多张），PDF / Word 请逐份上传')
  if (files.some(f => !/\.(pdf|docx|png|jpe?g)$/i.test(f.name))) throw new Error('仅支持 Word（.docx）、PDF、JPG / PNG 图片')
  if (files.some(f => f.size > MAX_FILE_SIZE)) throw new Error('单个文件不超过 50 MB')
  return types[0]
}

function notify(jobId: string) {
  const job = jobs.get(jobId)
  if (!job) return
  listeners.get(jobId)?.forEach(fn => fn(clone(job)))
}

function requireJob(jobId: string) {
  const job = jobs.get(jobId)
  if (!job) throw new Error('解析任务不存在')
  return job
}

function locate(questionId: string) {
  for (const [jobId, list] of questions) {
    const idx = list.findIndex(q => q.id === questionId)
    if (idx >= 0) return { jobId, list, idx }
  }
  throw new Error('题目不存在')
}

function renumber(jobId: string, list: DraftQuestion[]) {
  list.forEach((q, i) => (q.no = i + 1))
  questions.set(jobId, list)
  const job = requireJob(jobId)
  job.questionCount = list.length
  job.reviewCount = list.filter(q => q.confidence < REVIEW_CONFIDENCE).length
  return clone(list)
}

/** 按进度推进阶段：每个启用的阶段均分 0–100 */
function advance(job: ParseJob, qs: DraftQuestion[]) {
  job.progress = Math.min(100, job.progress + STEP)
  const active = job.stages.filter(s => s.status !== 'skipped')
  const span = 100 / active.length
  active.forEach((s, i) => {
    const end = span * (i + 1)
    if (job.progress >= end - 0.001) {
      s.status = 'done'
      s.note = STAGE_NOTES[s.stage](job, qs)
    } else if (job.progress >= end - span) {
      s.status = 'running'
    }
  })
  if (job.stages.find(s => s.stage === 'classify')?.status === 'done') job.meta ??= clone(MOCK_META)
  if (job.progress >= 100) {
    job.status = 'done'
    job.questionCount = qs.length
    job.reviewCount = qs.filter(q => q.confidence < REVIEW_CONFIDENCE).length
  }
}

function runPipeline(job: ParseJob) {
  const qs = buildMockQuestions(job.id, job.options)
  job.status = 'running'
  job.parser = 'mineru_cloud'
  const timer = setInterval(() => {
    advance(job, qs)
    if (job.status === 'done') {
      clearInterval(timer)
      questions.set(job.id, qs)
      const calls = buildMockUsage(job.pageCount ?? MOCK_PAGE_COUNT, qs.length)
      usage.set(job.id, calls)
      job.usage = summarizeMockUsage(calls)
      recent.unshift({
        jobId: job.id, fileName: job.fileName, questionCount: job.questionCount,
        reviewCount: job.reviewCount, savedCount: 0, createdAt: job.createdAt,
      })
    }
    notify(job.id)
  }, TICK_MS)
}

/** 小问标记：（1）(1) ⑴ */
const SUB_MARK = /(?:（\d+）|\(\d+\)|[⑴-⒇])/g

function splitByMarks(text: string) {
  const marks = [...text.matchAll(SUB_MARK)]
  if (marks.length < 2) return null
  const head = text.slice(0, marks[0].index)
  const parts = marks.map((m, i) => text.slice(m.index, marks[i + 1]?.index ?? text.length).trim())
  return { head, parts }
}

export const mockParseApi: ParseApi = {
  async createJob(files: File[], options: ParseOptions, onUploadProgress) {
    const fileType = validate(files)
    for (let p = 20; p <= 100; p += 20) {
      await sleep(80)
      onUploadProgress?.(p)
    }
    const id = 'job_' + Math.random().toString(36).slice(2, 10)
    const stages: StageState[] = PARSE_STAGES.map(stage => ({
      stage,
      status: stage === 'knowledge' && !options.knowledge ? 'skipped' : 'pending',
    }))
    const job: ParseJob = {
      id,
      fileName: files.length > 1 ? `${files[0].name} 等 ${files.length} 个文件` : files[0].name,
      fileCount: files.length,
      fileSize: files.reduce((a, f) => a + f.size, 0),
      fileType,
      pageCount: fileType === 'image' ? files.length : MOCK_PAGE_COUNT,
      options: { ...options },
      parser: null,
      status: 'queued',
      progress: 0,
      stages,
      meta: null,
      questionCount: 0,
      reviewCount: 0,
      savedCount: 0,
      usage: null,
      answerTask: null,
      createdAt: new Date().toISOString(),
    }
    jobs.set(id, job)
    setTimeout(() => runPipeline(job), 200)
    return clone(job)
  },

  async getJob(jobId) {
    return clone(requireJob(jobId))
  },

  subscribe(jobId, onEvent) {
    const set = listeners.get(jobId) ?? new Set()
    set.add(onEvent)
    listeners.set(jobId, set)
    queueMicrotask(() => notify(jobId))
    return () => set.delete(onEvent)
  },

  async updateMeta(jobId, meta) {
    await sleep(120)
    requireJob(jobId).meta = clone(meta)
    return clone(meta)
  },

  async listQuestions(jobId) {
    await sleep(150)
    return clone(questions.get(jobId) ?? [])
  },

  async updateQuestion(questionId, patch) {
    await sleep(120)
    const { jobId, list, idx } = locate(questionId)
    // 人工修改过的题视为已核对
    const touchesContent = ['stem', 'options', 'answer', 'type'].some(k => k in patch)
    const touchesAnswer = 'answer' in patch || 'analysis' in patch
    list[idx] = { ...list[idx], ...clone(patch), ...(touchesContent ? { confidence: 1 } : {}) }
    if (touchesAnswer) list[idx] = { ...list[idx], answerSource: list[idx].answer ? 'manual' : null, answerNote: null }
    renumber(jobId, list)
    return clone(list[idx])
  },

  async mergeWithPrevious(questionId) {
    await sleep(200)
    const { jobId, list, idx } = locate(questionId)
    if (idx === 0) throw new Error('第一题没有上一题可合并')
    const prev = list[idx - 1]
    const cur = list[idx]
    const score = prev.score + cur.score
    const kps = [...prev.knowledgePoints]
    cur.knowledgePoints.forEach(k => kps.some(p => p.id === k.id) || kps.push(k))
    list[idx - 1] = {
      ...prev,
      stem: prev.stem + '\n' + cur.stem,
      options: prev.options.length ? prev.options : cur.options,
      answer: [prev.answer, cur.answer].filter(Boolean).join('；') || null,
      analysis: [prev.analysis, cur.analysis].filter(Boolean).join('\n') || null,
      score,
      knowledgePoints: kps,
      coef: +((prev.coef * prev.score + cur.coef * cur.score) / score).toFixed(2),
      confidence: Math.min(prev.confidence, cur.confidence),
      blockIds: [...prev.blockIds, ...cur.blockIds],
      regions: [...prev.regions, ...cur.regions],
    }
    list.splice(idx, 1)
    return renumber(jobId, list)
  },

  async splitSubQuestions(questionId) {
    await sleep(200)
    const { jobId, list, idx } = locate(questionId)
    const q = list[idx]
    const stem = splitByMarks(q.stem)
    if (!stem) throw new Error('未识别到（1）（2）等小问标记，请先编辑题目')
    const answers = q.answer ? splitByMarks(q.answer)?.parts : undefined
    const n = stem.parts.length
    const each = Math.floor(q.score / n)
    const parts: DraftQuestion[] = stem.parts.map((p, i) => ({
      ...clone(q),
      id: `${q.id}_${i + 1}`,
      stem: stem.head + p,
      score: i === n - 1 ? q.score - each * (n - 1) : each,
      answer: answers?.[i] ?? (i === 0 ? q.answer : null),
      analysis: i === 0 ? q.analysis : null,
    }))
    list.splice(idx, 1, ...parts)
    return renumber(jobId, list)
  },

  async getSource(questionId) {
    await sleep(120)
    const { jobId, list, idx } = locate(questionId)
    const job = requireJob(jobId)
    const q = list[idx]
    const pages = [...new Set(q.regions.map(r => r.page))]
    return pages.map(page => ({
      page,
      url: mockPageImage(page, job.fileName),
      regions: q.regions.filter(r => r.page === page),
    }))
  },

  async commit(jobId, questionIds) {
    await sleep(300)
    const job = requireJob(jobId)
    const ids = new Set(questionIds)
    const list = questions.get(jobId) ?? []
    list.forEach(q => ids.has(q.id) && (q.status = 'saved'))
    job.savedCount = list.filter(q => q.status === 'saved').length
    const r = recent.find(r => r.jobId === jobId)
    if (r) {
      r.savedCount = job.savedCount
      r.questionCount = job.questionCount
      r.reviewCount = job.reviewCount
    }
    return { savedCount: ids.size }
  },

  async listRecent() {
    await sleep(100)
    return clone(recent.slice(0, 5))
  },

  async generateAnswers(jobId, { questionIds, overwrite = false } = {}) {
    await sleep(150)
    const job = requireJob(jobId)
    if (job.answerTask && ['queued', 'running'].includes(job.answerTask.status)) throw new Error('正在生成答案，请等待当前任务完成')
    const list = questions.get(jobId) ?? []
    const wanted = questionIds ? new Set(questionIds) : null
    const targets = list.filter(q => (!wanted || wanted.has(q.id)) && (overwrite || !q.answer))
    if (!targets.length) throw new Error('没有需要生成答案的题目')
    job.answerTask = { status: 'running', total: targets.length, done: 0, failed: 0, error: null, questionIds: targets.map(t => t.id) }
    // 逐题模拟生成，每题约 0.6 秒
    targets.forEach((t, i) => setTimeout(() => {
      const cur = questions.get(jobId)?.find(q => q.id === t.id)
      if (cur) Object.assign(cur, mockAiAnswer(cur), { answerSource: 'ai', status: 'draft' })
      job.answerTask!.done += 1
      if (job.answerTask!.done === job.answerTask!.total) job.answerTask!.status = 'done'
      notify(jobId)
    }, 600 * (i + 1)))
    return clone(job.answerTask)
  },

  async getUsage(jobId) {
    await sleep(100)
    requireJob(jobId)
    const calls = usage.get(jobId) ?? []
    return { summary: summarizeMockUsage(calls), calls: clone(calls) }
  },

  async getUsageOverview(days) {
    await sleep(100)
    // 历史记录按每份 4 页、9 题的示例用量计入
    const history = MOCK_RECENT.map(() => buildMockUsage(MOCK_PAGE_COUNT, 9))
    const all = [...history, ...usage.values()]
    const calls = all.flat()
    const summary = summarizeMockUsage(calls)
    const jobsN = all.length
    const pages = calls.reduce((a, c) => a + c.pages, 0)
    const questionsN = MOCK_RECENT.reduce((a, r) => a + r.questionCount, 0)
      + [...questions.values()].reduce((a, qs) => a + qs.length, 0)
    const overview: UsageOverview = {
      days,
      jobs: jobsN,
      pages,
      questions: questionsN,
      summary,
      costPerJob: summary.cost! / jobsN,
      costPerPage: pages ? summary.cost! / pages : null,
      costPerQuestion: questionsN ? summary.cost! / questionsN : null,
      tokensPerJob: Math.round(summary.totalTokens / jobsN),
      daily: [],
    }
    return overview
  },
}
