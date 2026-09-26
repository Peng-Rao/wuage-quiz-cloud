import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'
import {
  REVIEW_CONFIDENCE,
  parseApi,
  type CommitResult,
  type DraftQuestion,
  type DraftQuestionPatch,
  type GenerateAnswersOptions,
  type JobListItem,
  type JobUsage,
  type PaperMeta,
  type ParseJob,
  type ParseOptions,
  type RecentUpload,
  type SimilarQuestion,
  type SourceImage,
  type UsageOverview,
} from '@/api/parse'

export type ParsePhase = 'idle' | 'uploading' | 'parsing' | 'done' | 'failed'

/** 试卷解析：上传 → 进度订阅 → 草稿题核对 → 入库 */
export const useParseJobStore = defineStore('parseJob', () => {
  const phase = ref<ParsePhase>('idle')
  const options = reactive<ParseOptions>({ ocr: true, answer: true, dedupe: true, knowledge: true })
  const uploadPct = ref(0)
  const job = ref<ParseJob | null>(null)
  const questions = ref<DraftQuestion[]>([])
  const selected = ref(new Set<string>())
  const recent = ref<RecentUpload[]>([])
  const error = ref('')
  /** 正在请求中的题目 id，用于按钮禁用 */
  const busy = ref(new Set<string>())
  const savedCount = ref(0)
  const usage = ref<JobUsage | null>(null)
  const usageOverview = ref<UsageOverview | null>(null)
  const OVERVIEW_DAYS = 30

  // 任务列表（批量后台解析）
  const jobList = ref<JobListItem[]>([])
  const jobsTotal = ref(0)
  const jobsActive = ref(0)
  const batchUploading = ref(false)
  const batchPct = ref(0)
  /** 批量上传后的提示，如「已加入后台队列：3 份试卷」 */
  const notice = ref('')

  let unsubscribe: (() => void) | null = null
  let answerTimer: ReturnType<typeof setTimeout> | null = null
  let jobsTimer: ReturnType<typeof setTimeout> | null = null
  let watchingJobs = false

  const isLow = (q: DraftQuestion) => q.confidence < REVIEW_CONFIDENCE
  const reviewCount = computed(() => questions.value.filter(isLow).length)
  const selectedCount = computed(() => questions.value.filter((q) => selected.value.has(q.id)).length)
  const allSelected = computed(() => !!questions.value.length && selectedCount.value === questions.value.length)
  const missingAnswerCount = computed(() => questions.value.filter((q) => !q.answer).length)
  const missingKnowledgeCount = computed(() => questions.value.filter((q) => !q.knowledgePoints.length).length)
  const knowledgeRunning = computed(() => job.value?.stages.find((s) => s.stage === 'knowledge')?.status === 'running')
  let knowledgeTimer: ReturnType<typeof setTimeout> | null = null
  const answerTask = computed(() => job.value?.answerTask ?? null)
  const answering = computed(() => !!answerTask.value && ['queued', 'running'].includes(answerTask.value.status))
  /** 本次任务中尚未拿到答案的题 */
  const isAnswering = (q: DraftQuestion) => answering.value && !q.answer && !!answerTask.value?.questionIds.includes(q.id)

  /** 上传进度占总进度的前 10%，解析进度占后 90% */
  const overallPct = computed(() => {
    if (phase.value === 'uploading') return Math.round(uploadPct.value * 0.1)
    if (!job.value) return 0
    return Math.round(10 + job.value.progress * 0.9)
  })

  async function loadRecent() {
    const [r, o] = await Promise.all([
      parseApi.listRecent().catch(() => []),
      parseApi.getUsageOverview(OVERVIEW_DAYS).catch(() => null),
    ])
    recent.value = r
    usageOverview.value = o
  }

  async function loadUsage() {
    if (!job.value) return
    usage.value = await parseApi.getUsage(job.value.id).catch(() => null)
  }

  // ---------- 任务列表 ----------

  const JOBS_PAGE = 20

  async function refreshJobs() {
    try {
      const page = await parseApi.listJobs({ limit: JOBS_PAGE })
      jobList.value = page.items
      jobsTotal.value = page.total
      jobsActive.value = page.active
    } catch {
      // 列表刷新失败不打断当前操作，下次轮询重试
    }
    scheduleJobs()
  }

  /** 有排队或解析中的任务时，每 2 秒刷新列表 */
  function scheduleJobs() {
    if (jobsTimer) clearTimeout(jobsTimer)
    jobsTimer = null
    if (watchingJobs && jobsActive.value > 0) jobsTimer = setTimeout(refreshJobs, 2000)
  }

  /** 页面挂载时开启列表轮询，离开时关闭 */
  function watchJobs(on: boolean) {
    watchingJobs = on
    if (on) refreshJobs()
    else scheduleJobs()
  }

  /** 多份 PDF / Word：批量后台解析；其余（单份、或同一份试卷的多张图片）：单份解析 */
  const isBatch = (files: File[]) => files.length > 1 && files.every((f) => !/\.(png|jpe?g)$/i.test(f.name))

  async function startBatch(files: File[]) {
    error.value = ''
    notice.value = ''
    batchUploading.value = true
    batchPct.value = 0
    try {
      const batch = await parseApi.createBatch(files.map((f) => [f]), { ...options }, (p) => (batchPct.value = p))
      notice.value = `已加入后台队列：${batch.total} 份试卷，可以继续上传或点击列表查看`
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      batchUploading.value = false
      refreshJobs()
    }
  }

  /** 打开任意任务：已完成进入核对，未完成查看进度 */
  async function openJob(jobId: string) {
    reset()
    let next: ParseJob
    try {
      next = await parseApi.getJob(jobId)
    } catch (e) {
      error.value = (e as Error).message
      return
    }
    job.value = next
    if (next.status === 'done') {
      applyQuestions(await parseApi.listQuestions(jobId))
      selected.value = new Set(questions.value.filter((q) => q.status !== 'saved').map((q) => q.id))
      phase.value = 'done'
      loadUsage()
      if (next.answerTask && ['queued', 'running'].includes(next.answerTask.status)) {
        pollAnswers(next.answerTask.done + next.answerTask.failed)
      }
      if (knowledgeRunning.value) pollKnowledge()
    } else if (next.status === 'failed' || next.status === 'cancelled') {
      phase.value = 'failed'
      error.value = next.error ?? ''
    } else {
      phase.value = 'parsing'
      unsubscribe = parseApi.subscribe(jobId, onJobEvent)
    }
  }

  /** 回到上传页：只停止本页订阅，任务在后台继续 */
  function backToList() {
    reset()
    refreshJobs()
  }

  async function retryJob(jobId: string) {
    await withBusy('job:' + jobId, () => parseApi.retryJob(jobId))
    if (job.value?.id === jobId) await openJob(jobId)
    refreshJobs()
  }

  async function cancelJob(jobId: string) {
    await withBusy('job:' + jobId, () => parseApi.cancelJob(jobId))
    refreshJobs()
  }

  /** 设为评测样本（已是样本时用当前核对结果覆盖） */
  async function markEvalSample() {
    if (!job.value) return
    await withBusy('eval', async () => {
      const sample = await parseApi.markEvalSample(job.value!.id)
      job.value = { ...job.value!, evalSampleId: sample.id }
      notice.value = `已保存为评测样本（${sample.questionCount} 题），可在「解析评测」中运行`
    })
  }

  const getSimilar = (id: string): Promise<SimilarQuestion[] | undefined> =>
    withBusy('similar:' + id, () => parseApi.getSimilar(id, { limit: 8, scope: 'all' }))

  async function start(files: File[]) {
    if (isBatch(files)) return startBatch(files)
    reset()
    phase.value = 'uploading'
    try {
      job.value = await parseApi.createJob(files, { ...options }, (p) => (uploadPct.value = p))
    } catch (e) {
      phase.value = 'idle'
      error.value = (e as Error).message
      return
    }
    phase.value = 'parsing'
    unsubscribe = parseApi.subscribe(job.value.id, onJobEvent)
  }

  async function onJobEvent(next: ParseJob) {
    job.value = next
    if (next.status === 'failed' || next.status === 'cancelled') {
      stop()
      loadUsage()
      phase.value = 'failed'
      error.value = next.error ?? '解析失败，请稍后重试'
    } else if (next.status === 'done' && phase.value === 'parsing') {
      stop()
      applyQuestions(await parseApi.listQuestions(next.id))
      selected.value = new Set(questions.value.map((q) => q.id))
      phase.value = 'done'
      loadUsage()
      loadRecent()
      refreshJobs()
    }
  }

  function stop() {
    unsubscribe?.()
    unsubscribe = null
    if (answerTimer) clearTimeout(answerTimer)
    answerTimer = null
    if (knowledgeTimer) clearTimeout(knowledgeTimer)
    knowledgeTimer = null
  }

  /** AI 补标知识点：后台执行，轮询 knowledge 阶段直到结束后刷新题目 */
  async function tagKnowledge() {
    if (!job.value) return
    await withBusy('knowledge', async () => {
      job.value = await parseApi.tagKnowledge(job.value!.id)
      pollKnowledge()
    })
  }

  function pollKnowledge() {
    if (knowledgeTimer) clearTimeout(knowledgeTimer)
    knowledgeTimer = setTimeout(async () => {
      if (!job.value) return
      const jobId = job.value.id
      const next = await parseApi.getJob(jobId).catch(() => null)
      if (!next || job.value?.id !== jobId) return pollKnowledge()
      job.value = next
      if (knowledgeRunning.value) return pollKnowledge()
      applyQuestions(await parseApi.listQuestions(jobId))
      const st = next.stages.find((s) => s.stage === 'knowledge')
      if (st?.status === 'failed') error.value = st.note ?? '知识点标注失败'
      loadUsage()
    }, 1500)
  }

  /** 发起 AI 生成答案；默认补全所有缺答案的题 */
  async function generateAnswers(opts: GenerateAnswersOptions = {}) {
    if (!job.value) return
    error.value = ''
    const key = opts.questionIds?.length === 1 ? opts.questionIds[0] : 'answers'
    await withBusy(key, async () => {
      const task = await parseApi.generateAnswers(job.value!.id, opts)
      job.value = { ...job.value!, answerTask: task }
      pollAnswers(task.done + task.failed)
    })
  }

  /** 轮询生成进度：每有题目完成就刷新题目列表，答案逐题出现 */
  function pollAnswers(lastFinished: number) {
    if (answerTimer) clearTimeout(answerTimer)
    answerTimer = setTimeout(async () => {
      if (!job.value) return
      const jobId = job.value.id
      let next: ParseJob
      try {
        next = await parseApi.getJob(jobId)
      } catch {
        pollAnswers(lastFinished)
        return
      }
      if (job.value?.id !== jobId) return
      job.value = next
      const task = next.answerTask
      const finished = task ? task.done + task.failed : 0
      const running = !!task && ['queued', 'running'].includes(task.status)
      if (finished !== lastFinished || !running) applyQuestions(await parseApi.listQuestions(jobId))
      if (running) pollAnswers(finished)
      else {
        if (task?.status === 'failed' || task?.error) error.value = task.error ?? 'AI 生成答案失败'
        loadUsage()
      }
    }, 1500)
  }

  /** 合并 / 拆分后 id 会变化，保持原有选中状态，新产生的题默认选中 */
  function applyQuestions(list: DraftQuestion[]) {
    const prev = new Set(questions.value.map((q) => q.id))
    const sel = new Set<string>()
    for (const q of list) if (!prev.has(q.id) || selected.value.has(q.id)) sel.add(q.id)
    questions.value = list
    selected.value = sel
  }

  async function withBusy<T>(id: string, fn: () => Promise<T>): Promise<T | undefined> {
    busy.value = new Set(busy.value).add(id)
    error.value = ''
    try {
      return await fn()
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      const next = new Set(busy.value)
      next.delete(id)
      busy.value = next
    }
  }

  function updateQuestion(id: string, patch: DraftQuestionPatch) {
    return withBusy(id, async () => {
      const q = await parseApi.updateQuestion(id, patch)
      questions.value = questions.value.map((x) => (x.id === id ? q : x))
      return q
    })
  }

  const mergeWithPrevious = (id: string) =>
    withBusy(id, async () => applyQuestions(await parseApi.mergeWithPrevious(id)))

  const splitSubQuestions = (id: string) =>
    withBusy(id, async () => applyQuestions(await parseApi.splitSubQuestions(id)))

  const getSource = (id: string): Promise<SourceImage[] | undefined> =>
    withBusy(id, () => parseApi.getSource(id))

  async function updateMeta(meta: PaperMeta) {
    if (!job.value) return
    job.value = { ...job.value, meta: await parseApi.updateMeta(job.value.id, meta) }
    // 出处由分类信息拼出，修改后刷新
    applyQuestions(await parseApi.listQuestions(job.value.id))
  }

  function toggleSelect(id: string) {
    const next = new Set(selected.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    selected.value = next
  }

  function toggleAll() {
    selected.value = allSelected.value ? new Set() : new Set(questions.value.map((q) => q.id))
  }

  /** 最近一次保存的结果：跳过的重复题、已在试卷库中的同一份试卷 */
  const commitResult = ref<CommitResult | null>(null)

  /** 保存到校本题库。默认跳过与题库重复的题，整份试卷已入库时不保存；force 时全部保存（only 指定题目） */
  async function commit(force = false, only?: string[]) {
    if (!job.value) return
    const ids = only ?? questions.value.filter((q) => selected.value.has(q.id)).map((q) => q.id)
    if (!ids.length) return
    await withBusy('commit', async () => {
      const res = await parseApi.commit(job.value!.id, ids, force)
      const saved = new Set(res.savedIds)
      questions.value = questions.value.map((q) => (saved.has(q.id) ? { ...q, status: 'saved' } : q))
      // 强制保存跳过的题后，不再提示这些题
      const prevSkipped = force ? (commitResult.value?.skipped ?? []).filter((x) => !saved.has(x.questionId)) : []
      commitResult.value = { ...res, skipped: [...prevSkipped, ...res.skipped] }
      if (res.savedCount) savedCount.value = questions.value.filter((q) => q.status === 'saved').length
      if (res.savedCount) loadRecent()
    })
  }

  function reset() {
    stop()
    phase.value = 'idle'
    uploadPct.value = 0
    job.value = null
    questions.value = []
    selected.value = new Set()
    error.value = ''
    savedCount.value = 0
    commitResult.value = null
    usage.value = null
  }

  return {
    phase, options, uploadPct, overallPct, job, questions, selected, recent, error, busy, savedCount, commitResult,
    usage, usageOverview, OVERVIEW_DAYS, loadUsage,
    jobList, jobsTotal, jobsActive, batchUploading, batchPct, notice,
    refreshJobs, watchJobs, openJob, backToList, retryJob, cancelJob, getSimilar, markEvalSample,
    missingAnswerCount, answerTask, answering, isAnswering, generateAnswers,
    missingKnowledgeCount, knowledgeRunning, tagKnowledge,
    reviewCount, selectedCount, allSelected, isLow,
    loadRecent, start, stop, updateQuestion, mergeWithPrevious, splitSubQuestions, getSource, updateMeta,
    toggleSelect, toggleAll, commit, reset,
  }
})
