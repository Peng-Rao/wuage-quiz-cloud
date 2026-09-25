import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'
import {
  REVIEW_CONFIDENCE,
  parseApi,
  type DraftQuestion,
  type DraftQuestionPatch,
  type PaperMeta,
  type ParseJob,
  type ParseOptions,
  type RecentUpload,
  type SourceImage,
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

  let unsubscribe: (() => void) | null = null

  const isLow = (q: DraftQuestion) => q.confidence < REVIEW_CONFIDENCE
  const reviewCount = computed(() => questions.value.filter(isLow).length)
  const selectedCount = computed(() => questions.value.filter((q) => selected.value.has(q.id)).length)
  const allSelected = computed(() => !!questions.value.length && selectedCount.value === questions.value.length)

  /** 上传进度占总进度的前 10%，解析进度占后 90% */
  const overallPct = computed(() => {
    if (phase.value === 'uploading') return Math.round(uploadPct.value * 0.1)
    if (!job.value) return 0
    return Math.round(10 + job.value.progress * 0.9)
  })

  async function loadRecent() {
    recent.value = await parseApi.listRecent().catch(() => [])
  }

  async function start(files: File[]) {
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
    if (next.status === 'failed') {
      stop()
      phase.value = 'failed'
      error.value = next.error ?? '解析失败，请稍后重试'
    } else if (next.status === 'done' && phase.value === 'parsing') {
      stop()
      applyQuestions(await parseApi.listQuestions(next.id))
      selected.value = new Set(questions.value.map((q) => q.id))
      phase.value = 'done'
      loadRecent()
    }
  }

  function stop() {
    unsubscribe?.()
    unsubscribe = null
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

  async function commit() {
    if (!job.value || !selectedCount.value) return
    const ids = questions.value.filter((q) => selected.value.has(q.id)).map((q) => q.id)
    await withBusy('commit', async () => {
      const res = await parseApi.commit(job.value!.id, ids)
      savedCount.value = res.savedCount
      questions.value = questions.value.map((q) => (ids.includes(q.id) ? { ...q, status: 'saved' } : q))
      loadRecent()
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
  }

  return {
    phase, options, uploadPct, overallPct, job, questions, selected, recent, error, busy, savedCount,
    reviewCount, selectedCount, allSelected, isLow,
    loadRecent, start, stop, updateQuestion, mergeWithPrevious, splitSubQuestions, getSource, updateMeta,
    toggleSelect, toggleAll, commit, reset,
  }
})
