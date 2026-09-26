import {
  PARSE_STAGES,
  REVIEW_CONFIDENCE,
  type DraftQuestion,
  type DraftQuestionPatch,
  type JobListItem,
  type ParseApi,
  type ParseBatch,
  type SimilarQuestion,
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
  MOCK_META, MOCK_PAGE_COUNT, MOCK_RECENT, buildMockQuestions, buildMockUsage, mockAiAnswer, mockKnowledge,
  mockPageImage, mockSource, summarizeMockUsage,
} from './mockData'
import { QUESTIONS } from '@/data/mock'

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
const batches = new Map<string, { id: string; total: number; createdAt: string }>()
/** 创建顺序，列表按此倒序 */
const order: string[] = []

/** 同时解析的试卷数，其余排队（与后端 WORKER_CONCURRENCY 默认值一致） */
const MAX_RUNNING = 2
const waiting: string[] = []
let running = 0

const STAGE_NOTES: Record<ParseStage, (job: ParseJob, qs: DraftQuestion[]) => string> = {
  ocr: job => `识别 ${job.pageCount} 页`,
  classify: () => `${MOCK_META.stage} · ${MOCK_META.subject} · ${MOCK_META.paperType.replace('考试', '')}`,
  segment: (_, qs) => `${qs.length} 道题`,
  knowledge: (_, qs) => `${new Set(qs.flatMap(q => q.knowledgePoints.map(k => k.id))).size} 个知识点`,
  difficulty: () => '基线估计（越高越难）',
  dedupe: (_, qs) => {
    const n = qs.filter(q => q.duplicateOf).length
    return n ? `${n} 道疑似重复` : '未发现重复'
  },
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

/** 与后端一致：返回的题目带上按试卷分类拼出的出处 */
function withSource(jobId: string, q: DraftQuestion): DraftQuestion {
  const job = requireJob(jobId)
  return { ...clone(q), source: mockSource(job.meta, job.fileName, q.no, q.page) }
}

function renumber(jobId: string, list: DraftQuestion[]) {
  list.forEach((q, i) => (q.no = i + 1))
  questions.set(jobId, list)
  const job = requireJob(jobId)
  job.questionCount = list.length
  job.reviewCount = list.filter(q => q.confidence < REVIEW_CONFIDENCE).length
  return list.map(q => withSource(jobId, q))
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

function schedule() {
  while (running < MAX_RUNNING && waiting.length) {
    const job = jobs.get(waiting.shift()!)
    if (!job || job.status !== 'queued') continue
    running++
    runPipeline(job)
  }
}

function enqueue(job: ParseJob) {
  waiting.push(job.id)
  setTimeout(schedule, 200)
}

function runPipeline(job: ParseJob) {
  const qs = buildMockQuestions(job.id, job.options)
  job.status = 'running'
  job.parser = 'mineru_cloud'
  const timer = setInterval(() => {
    advance(job, qs)
    if (job.status === 'done') {
      clearInterval(timer)
      running--
      schedule()
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

function freshStages(options: ParseOptions): StageState[] {
  return PARSE_STAGES.map(stage => ({
    stage,
    status: (stage === 'knowledge' && !options.knowledge) || (stage === 'dedupe' && !options.dedupe) ? 'skipped' : 'pending',
  }))
}

function makeJob(files: File[], options: ParseOptions, batchId: string | null): ParseJob {
  const fileType = validate(files)
  const id = 'job_' + Math.random().toString(36).slice(2, 10)
  const job: ParseJob = {
      id,
      batchId,
      fileName: files.length > 1 ? `${files[0].name} 等 ${files.length} 个文件` : files[0].name,
      fileCount: files.length,
      fileSize: files.reduce((a, f) => a + f.size, 0),
      fileType,
      pageCount: fileType === 'image' ? files.length : MOCK_PAGE_COUNT,
      options: { ...options },
      parser: null,
      status: 'queued',
      progress: 0,
      stages: freshStages(options),
      meta: null,
      questionCount: 0,
      reviewCount: 0,
      savedCount: 0,
      usage: null,
      answerTask: null,
      createdAt: new Date().toISOString(),
  }
  jobs.set(id, job)
  order.push(id)
  return job
}

function toItem(job: ParseJob): JobListItem {
  return {
    id: job.id, batchId: job.batchId, fileName: job.fileName, fileType: job.fileType, status: job.status,
    progress: job.progress, currentStage: job.stages.find(s => s.status === 'running')?.stage ?? null,
    questionCount: job.questionCount, reviewCount: job.reviewCount, savedCount: job.savedCount,
    error: job.error ?? null, createdAt: job.createdAt,
  }
}

function batchOut(batchId: string): ParseBatch {
  const b = batches.get(batchId)
  if (!b) throw new Error('批量任务不存在')
  const list = order.map(id => jobs.get(id)!).filter(j => j.batchId === batchId)
  const counts: ParseBatch['counts'] = {}
  list.forEach(j => (counts[j.status] = (counts[j.status] ?? 0) + 1))
  return { ...b, counts, jobs: list.map(toItem) }
}

async function fakeUpload(onUploadProgress?: (pct: number) => void) {
  for (let p = 20; p <= 100; p += 20) {
    await sleep(80)
    onUploadProgress?.(p)
  }
}

// ---------- 相似题：二元字组合余弦（与后端字面相似度同思路） ----------

const norm = (t: string) => t.normalize('NFKC').replace(/[\s\p{P}$\\{}_]/gu, '').toLowerCase()
function bigrams(t: string) {
  const m = new Map<string, number>()
  for (let i = 0; i < t.length - 1; i++) m.set(t.slice(i, i + 2), (m.get(t.slice(i, i + 2)) ?? 0) + 1)
  return m
}
function lexical(a: string, b: string) {
  const fa = bigrams(norm(a)), fb = bigrams(norm(b))
  let dot = 0, na = 0, nb = 0
  fa.forEach((v, k) => { dot += v * (fb.get(k) ?? 0); na += v * v })
  fb.forEach(v => (nb += v * v))
  return na && nb ? dot / Math.sqrt(na * nb) : 0
}

/** 候选：示例题库 + 已入库的草稿题 + 其他试卷的草稿题 */
function candidates(excludeJob?: string): (Omit<SimilarQuestion, 'score' | 'lexical' | 'semantic' | 'duplicate'>)[] {
  const bank = QUESTIONS.map(q => ({
    id: 'bank_' + q.id, source: 'bank' as const, type: q.type, stem: q.stem,
    options: q.options.map(o => o.replace(/^[A-H]．/, '')), answer: q.answer, jobId: null, fileName: q.source,
    origin: { title: '', fileName: q.source, schoolYear: '', region: '', grade: '', paperType: '', subject: '', no: null, page: null, label: q.source },
    knowledgePoints: q.knowledge.split('；').map(name => ({ id: 'kp_' + name, name })),
  }))
  const drafts = [...questions.entries()].filter(([jid]) => jid !== excludeJob).flatMap(([jid, qs]) => qs.map(q => ({
    id: q.id, source: q.status === 'saved' ? 'bank' as const : 'draft' as const, type: q.type, stem: q.stem,
    options: q.options, answer: q.answer, jobId: jid, fileName: jobs.get(jid)?.fileName ?? null,
    origin: mockSource(jobs.get(jid)?.meta ?? null, jobs.get(jid)?.fileName ?? '', q.no, q.page),
    knowledgePoints: q.knowledgePoints,
  })))
  return [...bank, ...drafts]
}

function rank(text: string, pool: ReturnType<typeof candidates>, limit: number): SimilarQuestion[] {
  return pool
    .map(c => { const lex = lexical(text, c.stem + c.options.join('')); return { ...c, score: lex, lexical: lex, semantic: null, duplicate: lex >= 0.85 } })
    .filter(c => c.score >= 0.3)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
}

export const mockParseApi: ParseApi = {
  async createJob(files: File[], options: ParseOptions, onUploadProgress) {
    validate(files)
    await fakeUpload(onUploadProgress)
    const job = makeJob(files, options, null)
    enqueue(job)
    return clone(job)
  },

  async createBatch(papers, options, onUploadProgress) {
    papers.forEach(p => {
      try {
        validate(p)
      } catch (e) {
        throw new Error(`${p[0]?.name ?? ''}：${(e as Error).message}`)
      }
    })
    await fakeUpload(onUploadProgress)
    const id = 'batch_' + Math.random().toString(36).slice(2, 10)
    batches.set(id, { id, total: papers.length, createdAt: new Date().toISOString() })
    papers.forEach(p => enqueue(makeJob(p, options, id)))
    return clone(batchOut(id))
  },

  async getBatch(batchId) {
    await sleep(80)
    return clone(batchOut(batchId))
  },

  async listJobs({ status, batchId, limit = 20, offset = 0 } = {}) {
    await sleep(80)
    const all = [...order].reverse().map(id => jobs.get(id)!)
      .filter(j => (!status?.length || status.includes(j.status)) && (!batchId || j.batchId === batchId))
    const active = [...jobs.values()].filter(j => j.status === 'queued' || j.status === 'running').length
    return { items: clone(all.slice(offset, offset + limit).map(toItem)), total: all.length, active }
  },

  async cancelJob(jobId) {
    await sleep(80)
    const job = requireJob(jobId)
    if (job.status !== 'queued') throw new Error('只能取消排队中的任务')
    job.status = 'cancelled'
    job.error = '已取消'
    notify(jobId)
    return clone(job)
  },

  async retryJob(jobId) {
    await sleep(80)
    const job = requireJob(jobId)
    if (job.status !== 'failed' && job.status !== 'cancelled') throw new Error('只能重试失败或已取消的任务')
    Object.assign(job, { status: 'queued', error: undefined, progress: 0, stages: freshStages(job.options) })
    enqueue(job)
    return clone(job)
  },

  async getSimilar(questionId, { limit = 5 } = {}) {
    await sleep(150)
    const { jobId, list, idx } = locate(questionId)
    const q = list[idx]
    const pool = candidates(jobId).filter(c => c.type === q.type)
    const results = rank(q.stem + q.options.join(''), pool, limit)
    // 演示：查重命中的题补一条「题库中的同一道题」
    if (q.duplicateOf && !results.some(r => r.duplicate)) {
      results.unshift({ id: q.duplicateOf, source: 'bank', type: q.type, stem: q.stem, options: q.options, answer: q.answer,
        score: 0.97, lexical: 0.97, semantic: null, duplicate: true, jobId: null, fileName: '高一数学 9 月月考（学生版）.pdf',
        origin: mockSource({ ...MOCK_META, title: '高一数学 9 月月考', paperType: '月考' }, '', 2, 1), knowledgePoints: q.knowledgePoints })
    }
    return clone(results.slice(0, limit))
  },

  async searchSimilar(text, { limit = 10, type } = {}) {
    await sleep(150)
    return clone(rank(text, candidates().filter(c => !type || c.type === type), limit))
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
    return (questions.get(jobId) ?? []).map(q => withSource(jobId, q))
  },

  async tagKnowledge(jobId) {
    await sleep(100)
    const job = requireJob(jobId)
    const list = questions.get(jobId) ?? []
    if (list.every(q => q.knowledgePoints.length)) throw new Error('所有题目都已标注知识点')
    const stage = job.stages.find(s => s.stage === 'knowledge')!
    Object.assign(stage, { status: 'running', note: undefined })
    setTimeout(() => {
      const missing = list.filter(q => !q.knowledgePoints.length)
      missing.forEach(q => (q.knowledgePoints = mockKnowledge(q.no)))
      Object.assign(stage, { status: 'done', note: `补标 ${missing.length} 题` })
      notify(jobId)
    }, 1200)
    return clone(job)
  },

  async updateQuestion(questionId, patch) {
    await sleep(120)
    const { jobId, list, idx } = locate(questionId)
    // 人工修改过的题视为已核对
    // 只处理实际变化的字段（编辑弹窗会带上全部字段）
    const changed = (k: keyof DraftQuestionPatch) => k in patch && JSON.stringify(patch[k]) !== JSON.stringify(list[idx][k])
    const touchesContent = (['stem', 'options', 'answer', 'type'] as const).some(changed)
    const touchesAnswer = changed('answer') || changed('analysis')
    list[idx] = { ...list[idx], ...clone(patch), ...(touchesContent ? { confidence: 1 } : {}) }
    if (touchesAnswer) list[idx] = { ...list[idx], answerSource: list[idx].answer ? 'manual' : null, answerNote: null }
    renumber(jobId, list)
    return withSource(jobId, list[idx])
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
