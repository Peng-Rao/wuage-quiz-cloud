<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'
import ReviewPanel from '@/components/bank/ReviewPanel.vue'
const auth = useAuthStore()
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { TYPE_ORDER, coefToDiff } from '@/data/mock'
import { bankApi, type BankQuestion, type PaperDetail } from '@/api/bank'
import type { AnswerTask } from '@/api/parse'
import { useAppStore } from '@/stores/app'
import { fromBank, useBasketStore } from '@/stores/basket'
import QuestionCard from '@/components/bank/QuestionCard.vue'
import BankEditDialog from '@/components/bank/BankEditDialog.vue'
import DifficultyBar from '@/components/DifficultyBar.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import LoadingState from '@/components/LoadingState.vue'

const props = defineProps<{ id: string }>()
const router = useRouter()
const basket = useBasketStore()
const app = useAppStore()

const paper = ref<PaperDetail | null>(null)
const error = ref('')
// AI 补全答案的任务进度（在加载试卷时恢复，须先于下方的 watch 声明）
const answerTask = ref<AnswerTask | null>(null)
const answerNotice = ref('')
const answerError = ref('')
const answerStarting = ref(false)
const isRunning = (t: AnswerTask) => t.status === 'queued' || t.status === 'running'
const answering = computed(() => !!answerTask.value && isRunning(answerTask.value))
let answerTimer: ReturnType<typeof setTimeout> | null = null
let disposed = false
const isCurrentPaper = (id: string) => !disposed && props.id === id

watch(() => props.id, async (id) => {
  paper.value = null
  error.value = ''
  stopAnswerPoll()
  answerTask.value = null
  answerNotice.value = ''
  answerError.value = ''
  answerStarting.value = false
  try {
    const p = await bankApi.getPaper(id)
    if (isCurrentPaper(id)) paper.value = p
  } catch (e) {
    if (isCurrentPaper(id)) error.value = (e as Error).message
  }
  if (auth.isStaff && isCurrentPaper(id)) {
    // 接着显示进行中的生成任务（如刷新页面前发起的）
    try {
      const t = await bankApi.answerTask(id)
      if (isCurrentPaper(id) && t && isRunning(t)) {
        answerTask.value = t
        pollAnswers(id, t.done + t.failed)
      }
    } catch { /* 进度获取失败不影响查看试卷 */ }
  }
}, { immediate: true })

async function reloadPaper() {
  const id = props.id
  try {
    const p = await bankApi.getPaper(id)
    if (isCurrentPaper(id)) paper.value = p
  } catch (e) {
    if (isCurrentPaper(id)) error.value = (e as Error).message
  }
}

const qs = computed(() => paper.value?.questions ?? [])

// 管理员编辑题目
const editOpen = ref(false)
const editing = ref<{ q: BankQuestion; no: number } | null>(null)
function openEdit(q: BankQuestion, no: number) {
  editing.value = { q, no }
  editOpen.value = true
}
function onEdited(q: BankQuestion) {
  // 题型、分值可能变化，重新加载以更新题型统计与总分
  if (paper.value) paper.value = { ...paper.value, questions: paper.value.questions.map((x) => (x.id === q.id ? q : x)) }
  reloadPaper()
}
const diffCount = (d: string) => qs.value.filter((q) => coefToDiff(q.coef) === d).length
const types = computed(() => TYPE_ORDER.filter((t) => paper.value?.typeCounts[t])
  .map((t) => ({ t, n: paper.value!.typeCounts[t]!, score: qs.value.filter((q) => q.type === t).reduce((a, q) => a + q.score, 0) })))
const inBasket = computed(() => qs.value.filter((q) => basket.has(q.id)).length)
const tags = computed(() => {
  const m = paper.value?.meta
  return m ? [m.stage, m.grade, m.subject, m.paperType, m.region, m.schoolYear, m.school, m.textbook].filter(Boolean) : []
})

const allAns = ref(false)
const ansOpen = reactive<Record<string, boolean>>({})
const showAns = (id: string) => ansOpen[id] ?? allAns.value
function toggleAllAns() {
  allAns.value = !allAns.value
  for (const k of Object.keys(ansOpen)) delete ansOpen[k]
}

/** AI 补全答案：为已入库、缺少答案的题生成答案，写入题库后需重新审核 */
const missingAnswers = computed(() => qs.value.filter((q) => !q.answer?.trim()).length)
function stopAnswerPoll() {
  if (answerTimer) clearTimeout(answerTimer)
  answerTimer = null
}
onBeforeUnmount(() => {
  disposed = true
  stopAnswerPoll()
})

async function generateAnswers() {
  const id = props.id
  answerError.value = ''
  answerNotice.value = ''
  answerStarting.value = true
  try {
    const t = await bankApi.generateAnswers(id)
    if (!isCurrentPaper(id)) return
    answerTask.value = t
    pollAnswers(id, 0)
  } catch (e) {
    if (isCurrentPaper(id)) answerError.value = (e as Error).message
  } finally {
    if (isCurrentPaper(id)) answerStarting.value = false
  }
}

/** 轮询进度：每有题目完成就刷新试卷，答案逐题出现 */
function pollAnswers(id: string, lastFinished: number) {
  stopAnswerPoll()
  answerTimer = setTimeout(async () => {
    answerTimer = null
    if (!isCurrentPaper(id)) return
    let t: AnswerTask | null
    try {
      t = await bankApi.answerTask(id)
    } catch {
      if (isCurrentPaper(id)) pollAnswers(id, lastFinished)
      return
    }
    if (!isCurrentPaper(id)) return
    answerTask.value = t
    const finished = t ? t.done + t.failed : 0
    const running = !!t && isRunning(t)
    if (finished !== lastFinished || !running) await reloadPaper()
    if (!isCurrentPaper(id)) return
    if (running) return pollAnswers(id, finished)
    if (!t) return
    if (t.scope === 'bank') for (const qid of t.questionIds) ansOpen[qid] = true
    if (t.status === 'failed' || t.error) answerError.value = t.error ?? 'AI 生成答案失败'
    if (t.done) answerNotice.value = `已为 ${t.done} 道题生成答案（标记为「AI 生成」）。这些题已撤回审核，请核对后在下方「审核与分配题目」中重新审核。`
  }, 1500)
}

function addAll() {
  basket.addMany(qs.value.map(fromBank))
}

/** 用此卷组卷：试题篮换成本卷的题，保留原卷题序与分值 */
const confirmOpen = ref(false)
function useAsPaper() {
  if (basket.count && basket.items.some((x) => !qs.value.some((q) => q.id === x.q.id))) confirmOpen.value = true
  else replaceAndGo()
}
/** 移出试卷库（如重复入库的试卷） */
const removeOpen = ref(false)
const removing = ref(false)
async function removePaper() {
  removing.value = true
  try {
    await bankApi.removePaper(props.id)
    for (const q of qs.value) basket.remove(q.id)
    router.replace('/papers')
  } catch (e) {
    error.value = (e as Error).message
    removeOpen.value = false
  } finally {
    removing.value = false
  }
}

function replaceAndGo() {
  basket.replace(qs.value.map(fromBank))
  // 卷头学科等随当前学段学科显示，切换到本卷的学段学科
  const m = paper.value?.meta
  if (m?.stage && m.subject) app.pickSubject(m.stage, m.subject)
  confirmOpen.value = false
  router.push('/paper')
}
</script>

<template>
  <main class="detail container">
    <RouterLink to="/papers" class="back">‹ 试卷选题</RouterLink>

    <div v-if="error" class="card empty err">{{ error }}</div>
    <LoadingState v-else-if="!paper" class="card" label="正在加载整份试卷…" detail="正在获取题目、配图与答案解析" :rows="3" />

    <template v-else>
      <section class="card head">
        <div class="head-main">
          <h1 class="serif">{{ paper.title }}</h1>
          <div class="tags"><span v-for="t in tags" :key="t" class="chip is-soft">{{ t }}</span></div>
          <div class="stats">
            <div><b>{{ paper.questionCount }}</b><span>题</span></div>
            <div><b>{{ paper.totalScore }}</b><span>总分</span></div>
            <div v-if="paper.avgCoef !== null"><b>{{ paper.avgCoef.toFixed(2) }}</b><span>难度 · {{ coefToDiff(paper.avgCoef) }}</span></div>
            <div class="types">
              <span v-for="x in types" :key="x.t">{{ x.t }} {{ x.n }} 题 · {{ x.score }} 分</span>
            </div>
          </div>
          <div class="dist-wrap">
            <DifficultyBar :easy="diffCount('容易')" :mid="diffCount('适中')" :hard="diffCount('较难')" />
          </div>
          <p v-if="paper.sourceQuestionCount > paper.questionCount" class="partial">
            原卷共 {{ paper.sourceQuestionCount }} 题，已入库 {{ paper.questionCount }} 题；其余题目可在「试卷解析」中核对后保存。
          </p>
        </div>
        <div class="head-ops">
          <button class="btn btn-primary" @click="useAsPaper">用此卷组卷</button>
          <button class="btn btn-outline" :disabled="inBasket === qs.length" @click="addAll">
            {{ inBasket === qs.length ? '已全部加入试题篮' : '整卷加入试题篮' }}
          </button>
          <span class="basket-note">本卷 {{ inBasket }} / {{ qs.length }} 题在试题篮中</span>
          <button v-if="auth.isStaff" class="btn-link remove" @click="removeOpen = true">移出试卷库</button>
        </div>
      </section>

      <div class="toolbar">
        <span>按原卷题序</span>
        <div class="tools">
          <template v-if="auth.isStaff">
            <LoadingState v-if="answering && answerTask" compact class="ai-progress" :label="`AI 解答中 ${answerTask.done + answerTask.failed} / ${answerTask.total}`" />
            <LoadingState v-else-if="answerStarting" compact class="ai-progress" label="正在申请 AI 解答任务…" />
            <button
              v-else-if="missingAnswers" class="ai-gen"
              title="为缺少答案的题生成答案与解析，结果标记为「AI 生成」；生成后这些题需重新审核"
              @click="generateAnswers"
            >AI 补全答案（{{ missingAnswers }} 题）</button>
          </template>
          <button class="all-ans" @click="toggleAllAns">{{ allAns ? '收起全部解析' : '展开全部解析' }}</button>
        </div>
      </div>
      <div v-if="answerNotice" class="card ok-bar" role="status">
        <span>{{ answerNotice }}</span>
        <button class="btn-link" @click="answerNotice = ''">知道了</button>
      </div>
      <div v-if="answerError" class="card err-bar" role="alert">
        <span>{{ answerError }}</span>
        <button class="btn-link" @click="answerError = ''">知道了</button>
      </div>

      <div class="list">
        <QuestionCard
          v-for="(q, i) in qs" :key="q.id" :q="q" :no="q.source?.no ?? i + 1" in-paper
          :show-answer="showAns(q.id)" :in-basket="basket.has(q.id)"
          @toggle-answer="ansOpen[q.id] = !showAns(q.id)" @toggle-basket="basket.toggle(fromBank(q))"
          :can-edit="auth.isAdmin" @edit="openEdit(q, q.source?.no ?? i + 1)"
        />
      </div>
    </template>

    <BankEditDialog v-model="editOpen" :q="editing?.q ?? null" :no="editing?.no" @saved="onEdited" />

    <ReviewPanel v-if="auth.isStaff && paper" :questions="qs" @updated="reloadPaper" />

    <ModalDialog v-model="removeOpen" title="移出试卷库" :width="420">
      <p class="confirm">
        将从校本题库删除本卷已入库的 {{ qs.length }} 道题，试卷库中不再显示这份试卷。原卷的解析结果保留，可在「试卷解析」中重新保存。
      </p>
      <template #footer>
        <button class="btn" @click="removeOpen = false">取消</button>
        <button class="btn btn-danger" :disabled="removing" @click="removePaper">{{ removing ? '移出中…' : '移出试卷库' }}</button>
      </template>
    </ModalDialog>

    <ModalDialog v-model="confirmOpen" title="用此卷组卷" :width="420">
      <p class="confirm">试题篮中已有 {{ basket.count }} 道题，将被替换为本卷的 {{ qs.length }} 道题（保留原卷题序与分值）。</p>
      <template #footer>
        <button class="btn" @click="confirmOpen = false">取消</button>
        <button class="btn btn-primary" @click="replaceAndGo">替换并组卷</button>
      </template>
    </ModalDialog>
  </main>
</template>

<style scoped>
.detail { padding-top: 18px; padding-bottom: 56px; display: flex; flex-direction: column; gap: 14px; max-width: 1000px; }
.back { font-size: 13px; align-self: flex-start; }
.empty { padding: 40px; text-align: center; color: var(--c-text-4); font-size: 14px; }
.empty.err { color: var(--c-hard); }

.head { padding: 22px 24px; display: flex; flex-wrap: wrap; gap: 20px; }
.head-main { flex: 1 1 420px; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.head h1 { margin: 0; font-size: 21px; line-height: 1.5; font-weight: 700; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tags .chip { cursor: default; font-size: 12px; padding: 3px 8px; }
.stats { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px 28px; }
.stats > div { display: flex; align-items: baseline; gap: 4px; }
.stats b { font-size: 22px; font-weight: 700; color: var(--c-ink); }
.stats span { font-size: 12px; color: var(--c-text-3); }
.stats .types { flex-wrap: wrap; gap: 4px 12px; }
.dist-wrap { max-width: 360px; display: flex; flex-direction: column; gap: 6px; }
.partial { margin: 0; font-size: 12px; color: #B5661B; }
.head-ops { flex: 0 0 200px; display: flex; flex-direction: column; gap: 10px; justify-content: center; }
.head-ops .btn:disabled { opacity: .55; cursor: default; }
.remove { align-self: center; font-size: 12px; color: var(--c-text-4); }
.remove:hover { color: var(--c-hard); }
.btn-danger { background: var(--c-hard); border-color: var(--c-hard); color: #fff; }
.basket-note { font-size: 12px; color: var(--c-text-4); text-align: center; }

.toolbar { display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: var(--c-text-3); padding: 0 4px; }
.tools { display: flex; align-items: center; flex-wrap: wrap; justify-content: flex-end; gap: 10px; }
.ai-gen {
  border: 1px solid var(--c-primary); background: #fff; color: var(--c-primary); border-radius: var(--r-sm);
  padding: 4px 10px; font-size: 13px; font-weight: 600;
}
.ai-gen:hover { background: var(--c-primary-soft); }
.ai-progress { font-size: 13px; color: var(--c-primary); background: var(--c-primary-soft); border-radius: var(--r-sm); padding: 4px 10px; }
.ok-bar, .err-bar { padding: 10px 18px; display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 13px; line-height: 1.7; }
.ok-bar { color: #3F7340; background: #E9F1E7; border-color: #C8DCC4; }
.err-bar { color: #A0301F; background: #FBEAE6; border-color: #EFC2B8; }
.ok-bar .btn-link, .err-bar .btn-link { flex-shrink: 0; }
.all-ans { border: 1px solid var(--c-border); background: #fff; border-radius: var(--r-sm); padding: 4px 10px; font-size: 13px; color: var(--c-text-2); }
.list { display: flex; flex-direction: column; gap: 14px; }
.confirm { margin: 0; font-size: 14px; line-height: 1.8; color: var(--c-text-2); }

@media (max-width: 640px) {
  .head { padding: 18px; }
  .head-ops { flex: 1 1 100%; }
}
</style>
