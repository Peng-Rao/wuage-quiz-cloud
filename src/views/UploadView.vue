<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  QUESTION_TYPES, type DraftQuestion, type DraftQuestionPatch, type ParseOptions, type SimilarQuestion, type SourceImage,
} from '@/api/parse'
import { DIFF_COEFS, coefToDiff, type Difficulty } from '@/data/mock'
import { useBasketStore } from '@/stores/basket'
import { useParseJobStore } from '@/stores/parseJob'
import ToggleSwitch from '@/components/ToggleSwitch.vue'
import DifficultyBar from '@/components/DifficultyBar.vue'
import ParseProgressCard from '@/components/parse/ParseProgressCard.vue'
import PaperMetaPanel from '@/components/parse/PaperMetaPanel.vue'
import ParsedQuestionCard from '@/components/parse/ParsedQuestionCard.vue'
import EditQuestionDialog from '@/components/parse/EditQuestionDialog.vue'
import SourceImageDialog from '@/components/parse/SourceImageDialog.vue'
import UsageCard from '@/components/parse/UsageCard.vue'
import CostOverviewCard from '@/components/parse/CostOverviewCard.vue'
import JobListPanel from '@/components/parse/JobListPanel.vue'
import SimilarDialog from '@/components/parse/SimilarDialog.vue'

const store = useParseJobStore()
const { phase, job, questions, selected, error, busy, options, usage, usageOverview, jobList, notice } = storeToRefs(store)
const basket = useBasketStore()
const route = useRoute()
const router = useRouter()

// 地址栏 ?job= 是当前试卷的唯一来源：打开 / 返回都只改地址栏，由路由驱动 store，刷新后仍停留在同一份试卷
const routeJob = () => (typeof route.query.job === 'string' ? route.query.job : '')

function syncFromRoute() {
  const id = routeJob()
  if (id) {
    if (id !== job.value?.id) store.openJob(id)
  } else if (job.value && phase.value !== 'uploading') {
    store.backToList()
  }
}

onMounted(() => {
  store.loadRecent()
  store.watchJobs(true)
  syncFromRoute()
})
onBeforeUnmount(() => store.watchJobs(false))
watch(() => route.query.job, syncFromRoute)
// 单份上传创建出任务后补写地址栏；任务被清空时不反向改地址栏，避免与打开其他任务互相干扰
watch(() => job.value?.id, (id) => {
  if (id && routeJob() !== id) router.replace({ query: { ...route.query, job: id } })
})

function openJob(id: string) {
  if (routeJob() === id) store.openJob(id)
  else router.push({ query: { ...route.query, job: id } })
}

function leaveJob() {
  if (routeJob()) router.push({ query: { ...route.query, job: undefined } })
  else store.backToList()
}

const UP_OPT_LABELS: [keyof ParseOptions, string][] = [
  ['ocr', '图片 / 扫描件文字识别'], ['answer', '识别并关联答案解析'], ['dedupe', '与题库查重，合并重复题'], ['knowledge', '自动标注知识点'],
]

const STEPS = ['1 上传试卷', '2 智能解析', '3 核对入库']
const stepIdx = computed(() => ({ idle: 0, uploading: 1, parsing: 1, failed: 1, done: 2 })[phase.value])

// ---- 上传 ----
const fileInput = ref<HTMLInputElement>()
const dragging = ref(false)
const ACCEPT = '.docx,.pdf,.jpg,.jpeg,.png'
const MAX_MB = 50
const picked = ref({ label: '', size: '' })

function formatSize(bytes: number) {
  return bytes >= 1024 * 1024 ? (bytes / 1024 / 1024).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB'
}

function onFiles(list: FileList | null | undefined) {
  const files = [...(list ?? [])]
  if (!files.length) return
  picked.value = {
    label: files.length > 1 ? `${files[0].name} 等 ${files.length} 个文件` : files[0].name,
    size: formatSize(files.reduce((a, f) => a + f.size, 0)),
  }
  store.start(files)
}

function onDrop(e: DragEvent) {
  dragging.value = false
  onFiles(e.dataTransfer?.files)
}

// 相似题
const similarOpen = ref(false)
const similarTitle = ref('')
const similarItems = ref<SimilarQuestion[] | null>(null)
const similarError = ref('')
async function openSimilar(q: DraftQuestion) {
  similarTitle.value = `第 ${q.no} 题 · 相似题`
  similarItems.value = null
  similarError.value = ''
  similarOpen.value = true
  const got = await store.getSimilar(q.id)
  if (got) similarItems.value = got
  else similarError.value = error.value || '查找相似题失败'
}

// ---- 核对 ----
const ptab = ref('全部')
const PTABS = ['全部', ...QUESTION_TYPES, '待核对']

const shown = computed(() => questions.value.filter((q) =>
  ptab.value === '全部' ? true : ptab.value === '待核对' ? store.isLow(q) : q.type === ptab.value,
))

const cnt = (d: Difficulty) => questions.value.filter((q) => coefToDiff(q.coef) === d).length
const avgCoef = computed(() => {
  const tot = questions.value.reduce((a, q) => a + q.score, 0)
  return tot ? questions.value.reduce((a, q) => a + q.coef * q.score, 0) / tot : 0
})
const kpCover = computed(() => {
  const kp: Record<string, number> = {}
  for (const q of questions.value) {
    const first = q.knowledgePoints[0]?.name
    if (first) kp[first] = (kp[first] ?? 0) + q.score
  }
  const arr = Object.entries(kp).sort((a, b) => b[1] - a[1]).slice(0, 6)
  const max = arr[0]?.[1] ?? 1
  return arr.map(([name, score]) => ({ name, score, w: (score / max) * 100 + '%' }))
})

function cycleType(q: DraftQuestion) {
  store.updateQuestion(q.id, { type: QUESTION_TYPES[(QUESTION_TYPES.indexOf(q.type) + 1) % QUESTION_TYPES.length] })
}
function cycleDiff(q: DraftQuestion) {
  const i = DIFF_COEFS.findIndex((c) => coefToDiff(c) === coefToDiff(q.coef))
  store.updateQuestion(q.id, { coef: DIFF_COEFS[(i + 1) % DIFF_COEFS.length] })
}

// 编辑
const editOpen = ref(false)
const editing = ref<DraftQuestion | null>(null)
function openEdit(q: DraftQuestion) {
  editing.value = q
  editOpen.value = true
}
async function saveEdit(patch: DraftQuestionPatch) {
  if (!editing.value) return
  if (await store.updateQuestion(editing.value.id, patch)) editOpen.value = false
}

// 原图
const sourceOpen = ref(false)
const sourceTitle = ref('')
const sourceImages = ref<SourceImage[] | null>(null)
async function openSource(q: DraftQuestion) {
  sourceTitle.value = `第 ${q.no} 题 · 原图`
  sourceImages.value = null
  sourceOpen.value = true
  sourceImages.value = (await store.getSource(q.id)) ?? []
}

// 底部操作
const added = ref(false)
function addToBasket() {
  basket.addMany(questions.value.filter((q) => selected.value.has(q.id)).map((q) => q.id))
  added.value = true
  setTimeout(() => (added.value = false), 1600)
}
const note = computed(() =>
  store.savedCount ? `已保存 ${store.savedCount} 题到校本题库` : added.value ? '已加入试题篮' : '',
)
</script>

<template>
  <main class="upload container">
    <div class="top">
      <div class="intro">
        <h1>试卷解析</h1>
        <span>上传整份试卷，自动识别学科与试卷类型，拆分为单题，并评估每题难度。</span>
      </div>
      <nav class="tools">
        <RouterLink to="/upload/knowledge">知识树管理</RouterLink>
        <RouterLink to="/upload/eval">解析评测</RouterLink>
      </nav>
      <div class="steps">
        <span
          v-for="(s, i) in STEPS" :key="s" class="step"
          :class="{ cur: i === stepIdx, past: i < stepIdx }"
        >{{ s }}</span>
      </div>
    </div>

    <!-- 1 上传 -->
    <div v-if="phase === 'idle'" class="row">
      <div
        class="drop" :class="{ dragging }" role="button" tabindex="0"
        @click="fileInput?.click()" @keydown.enter.prevent="fileInput?.click()" @keydown.space.prevent="fileInput?.click()"
        @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="onDrop"
      >
        <div class="drop-icon">＋</div>
        <span class="drop-title">拖拽试卷到此处，或点击选择文件</span>
        <span class="drop-desc">
          支持 Word（.docx）、PDF、图片（JPG / PNG，可多张拍照）<br>
          可一次选择多份 PDF / Word，在后台依次解析 · 单个文件不超过 {{ MAX_MB }} MB
        </span>
        <button type="button" class="btn btn-primary pick-btn" :disabled="store.batchUploading">
          {{ store.batchUploading ? `上传中 ${store.batchPct}%` : '选择文件' }}
        </button>
        <span v-if="error" class="drop-err">{{ error }}</span>
        <span v-else-if="notice" class="drop-ok">{{ notice }}</span>
      </div>
      <input
        ref="fileInput" type="file" :accept="ACCEPT" multiple hidden
        @change="onFiles(($event.target as HTMLInputElement).files); ($event.target as HTMLInputElement).value = ''"
      >
      <div class="up-side">
        <div class="card panel">
          <span class="card-title">解析选项</span>
          <ToggleSwitch v-for="[k, l] in UP_OPT_LABELS" :key="k" v-model="options[k]" :label="l" />
        </div>
        <JobListPanel
          :jobs="jobList" :total="store.jobsTotal" :active="store.jobsActive" :busy="busy"
          @open="openJob" @retry="store.retryJob" @cancel="store.cancelJob"
        />
        <CostOverviewCard :overview="usageOverview" />
      </div>
    </div>

    <!-- 2 上传 / 解析中 -->
    <ParseProgressCard
      v-else-if="phase !== 'done'"
      :phase="phase" :job="job" :pct="store.overallPct" :upload-pct="store.uploadPct"
      :file-label="picked.label" :file-size="picked.size" :error="error"
      @retry="job && store.retryJob(job.id)" @back="leaveJob" @background="leaveJob"
    />

    <!-- 3 核对 -->
    <div v-else class="row">
      <aside class="review-side sticky-side">
        <PaperMetaPanel v-if="job?.meta" :meta="job.meta" @change="store.updateMeta" />
        <div class="card panel">
          <span class="card-title">难度评估</span>
          <div class="avg">
            <span class="avg-num">{{ avgCoef.toFixed(2) }}</span>
            <span class="avg-label">整卷难度系数 · {{ coefToDiff(avgCoef) }}</span>
          </div>
          <DifficultyBar :easy="cnt('容易')" :mid="cnt('适中')" :hard="cnt('较难')" :height="10" show-counts />
          <span class="hint">难度系数 0–1，越高越难，1 为最难（约等于 1 − 预估得分率）。依据知识点层级、解题步数与同类题历史作答数据估算。</span>
        </div>
        <UsageCard :usage="usage" :question-count="questions.length" />
        <div v-if="kpCover.length" class="card panel kp-panel">
          <span class="card-title">知识点分值</span>
          <div v-for="k in kpCover" :key="k.name" class="kp">
            <div class="kp-row"><span>{{ k.name }}</span><span class="muted-2">{{ k.score }} 分</span></div>
            <div class="kp-track"><div :style="{ width: k.w }" /></div>
          </div>
        </div>
      </aside>

      <section class="parsed">
        <div class="card summary">
          <span class="summary-count">已拆分 <b>{{ questions.length }}</b> 道题</span>
          <span v-if="store.reviewCount" class="warn">{{ store.reviewCount }} 道题识别置信度较低，建议核对</span>
          <span v-if="store.answering && store.answerTask" class="ai-progress">
            AI 解答中 {{ store.answerTask.done + store.answerTask.failed }} / {{ store.answerTask.total }}
          </span>
          <button
            v-else-if="store.missingAnswerCount" class="ai-gen" :disabled="busy.has('answers')"
            title="为缺少答案的题生成答案与解析，结果会标记为「AI 生成」，请老师核对"
            @click="store.generateAnswers()"
          >AI 生成答案（{{ store.missingAnswerCount }} 题）</button>
          <span v-if="store.knowledgeRunning" class="ai-progress">AI 标注知识点中…</span>
          <button
            v-else-if="store.missingKnowledgeCount" class="ai-gen" :disabled="busy.has('knowledge')"
            title="为尚未标注知识点的题标注 1–3 个知识点"
            @click="store.tagKnowledge()"
          >AI 标注知识点（{{ store.missingKnowledgeCount }} 题）</button>
          <div class="ptabs">
            <button v-for="t in PTABS" :key="t" class="chip" :class="{ 'is-soft': ptab === t, dark: ptab === t }" @click="ptab = t">{{ t }}</button>
          </div>
        </div>
        <div v-if="notice && phase === 'done'" class="card ok-bar">
          <span>{{ notice }}</span>
          <button class="btn-link" @click="notice = ''">知道了</button>
        </div>
        <div v-if="error" class="card err-bar" role="alert">
          <span>{{ error }}</span>
          <button class="btn-link" @click="error = ''">知道了</button>
        </div>

        <ParsedQuestionCard
          v-for="q in shown" :key="q.id"
          :q="q" :selected="selected.has(q.id)" :busy="busy.has(q.id)" :low="store.isLow(q)" :is-first="q.no === 1"
          :answering="store.isAnswering(q)"
          @toggle="store.toggleSelect(q.id)"
          @cycle-type="cycleType(q)" @cycle-diff="cycleDiff(q)"
          @edit="openEdit(q)" @merge="store.mergeWithPrevious(q.id)" @split="store.splitSubQuestions(q.id)"
          @source="openSource(q)" @similar="openSimilar(q)" @ai-answer="store.generateAnswers({ questionIds: [q.id] })"
        />
        <div v-if="!shown.length" class="card empty">当前筛选下没有题目</div>

        <div class="actionbar">
          <span class="sel">已选 <b>{{ store.selectedCount }}</b> / {{ questions.length }} 题</span>
          <button class="ab-link" @click="store.toggleAll()">{{ store.allSelected ? '取消全选' : '全选' }}</button>
          <span class="ab-note">{{ note }}</span>
          <div class="ab-actions">
            <button class="ab-btn ghost" @click="leaveJob">返回任务列表</button>
            <button
              class="ab-btn ghost" :disabled="busy.has('eval')"
              title="把当前核对结果保存为标准答案，用于评测解析效果"
              @click="store.markEvalSample()"
            >{{ job?.evalSampleId ? '更新评测样本' : '设为评测样本' }}</button>
            <button class="ab-btn light" :disabled="!store.selectedCount" @click="addToBasket">加入试题篮</button>
            <button class="ab-btn primary" :disabled="!store.selectedCount || busy.has('commit')" @click="store.commit()">
              {{ busy.has('commit') ? '保存中…' : '保存到校本题库' }}
            </button>
          </div>
        </div>
      </section>
    </div>

    <EditQuestionDialog v-model="editOpen" :job-id="job?.id ?? ''" :q="editing" :saving="!!editing && busy.has(editing.id)" @save="saveEdit" />
    <SourceImageDialog v-model="sourceOpen" :title="sourceTitle" :images="sourceImages" />
    <SimilarDialog v-model="similarOpen" :title="similarTitle" :items="similarItems" :error="similarError" />
  </main>
</template>

<style scoped>
.upload { width: 100%; padding-top: 24px; padding-bottom: 56px; display: flex; flex-direction: column; gap: 18px; }
.top { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; }
.intro { display: flex; flex-direction: column; gap: 6px; }
.intro h1 { margin: 0; font-size: 24px; font-weight: 700; }
.intro span { font-size: 14px; color: var(--c-text-3); }
.tools { margin-left: auto; display: flex; gap: 14px; font-size: 13px; }
.tools a { color: var(--c-text-2); }
.tools a:hover { color: var(--c-primary); }
.steps { display: flex; gap: 6px; font-size: 13px; }
.step { padding: 5px 12px; border-radius: 14px; background: var(--c-divider); color: var(--c-text-3); }
.step.past { background: var(--c-primary-soft); color: var(--c-primary-dark); }
.step.cur { background: var(--c-primary); color: #fff; }

.row { display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; }
.muted-2 { color: var(--c-text-3); }
.panel { padding: 18px; display: flex; flex-direction: column; gap: 12px; }

/* 上传区 */
.drop {
  flex: 999 1 520px; min-height: 340px; background: #fff; border: 2px dashed var(--c-primary-line); border-radius: 14px;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; cursor: pointer;
  padding: 32px; text-align: center; transition: background .15s, border-color .15s;
}
.drop:hover, .drop.dragging { background: #FDF6F0; border-color: var(--c-primary); }
.drop-icon {
  width: 56px; height: 56px; border-radius: 14px; background: var(--c-primary-soft); color: var(--c-primary);
  display: flex; align-items: center; justify-content: center; font-size: 28px;
}
.drop-title { font-size: 17px; font-weight: 600; }
.drop-desc { font-size: 13px; color: var(--c-text-3); line-height: 1.7; }
.pick-btn { margin-top: 6px; height: 40px; padding: 0 24px; font-size: 14px; }
.drop-err { font-size: 13px; color: #A0301F; }
.drop-ok { font-size: 13px; color: #3F7340; }
.pick-btn:disabled { opacity: .7; cursor: progress; }
.up-side { flex: 1 0 300px; display: flex; flex-direction: column; gap: 14px; }

/* 核对 · 左侧 */
.review-side { flex: 1 0 280px; max-width: 320px; display: flex; flex-direction: column; gap: 14px; }
/* 侧栏吸顶；内容超出屏幕高度时单独滚动，避免下方卡片永远看不到 */
.review-side.sticky-side { max-height: calc(100vh - 96px); overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; }
.avg { display: flex; align-items: baseline; gap: 8px; }
.avg-num { font-size: 32px; font-weight: 700; color: var(--c-primary); line-height: 1; }
.avg-label { font-size: 13px; color: var(--c-text-3); }
.hint { font-size: 12px; color: var(--c-text-3); line-height: 1.6; }
.kp-panel { gap: 10px; }
.kp { display: flex; flex-direction: column; gap: 4px; }
.kp-row { display: flex; justify-content: space-between; font-size: 13px; }
.kp-track { height: 4px; border-radius: 2px; background: var(--c-divider); }
.kp-track > div { height: 100%; border-radius: 2px; background: #E9A877; }

/* 核对 · 题目列表 */
.parsed { flex: 999 1 520px; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.summary { padding: 14px 18px; display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; }
.summary-count { font-size: 14px; }
.summary-count b { color: var(--c-primary); }
.warn { font-size: 13px; color: var(--c-primary-dark); background: var(--c-primary-soft); border-radius: var(--r-sm); padding: 3px 10px; }
.ptabs { margin-left: auto; display: flex; gap: 4px; flex-wrap: wrap; }
.ai-gen {
  border: 1px solid var(--c-primary); background: #fff; color: var(--c-primary); border-radius: var(--r-sm);
  padding: 3px 10px; font-size: 13px; font-weight: 600;
}
.ai-gen:hover:not(:disabled) { background: var(--c-primary-soft); }
.ai-gen:disabled { opacity: .5; cursor: not-allowed; }
.ai-progress { font-size: 13px; color: var(--c-primary); background: var(--c-primary-soft); border-radius: var(--r-sm); padding: 3px 10px; }
.chip.dark { color: var(--c-primary-dark); }
.err-bar {
  padding: 10px 18px; display: flex; align-items: center; justify-content: space-between; gap: 12px;
  font-size: 13px; color: #A0301F; background: #FBEAE6; border-color: #EFC2B8;
}
.ok-bar { padding: 10px 18px; display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 13px; color: #3F7340; background: #E9F1E7; border-color: #C8DCC4; }
.empty { padding: 32px; text-align: center; font-size: 14px; color: var(--c-text-4); }

.actionbar {
  position: sticky; bottom: 16px; background: var(--c-ink); color: #fff; border-radius: var(--r-lg);
  padding: 12px 16px 12px 20px; display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px;
  box-shadow: 0 10px 30px rgba(27, 36, 48, .25);
}
.sel { font-size: 14px; }
.sel b { color: var(--c-highlight); }
.ab-link { border: none; background: transparent; color: #D8DCE0; font-size: 13px; }
.ab-note { font-size: 13px; color: var(--c-highlight); }
.ab-actions { margin-left: auto; display: flex; gap: 8px; flex-wrap: wrap; }
.ab-btn { border-radius: var(--r-md); height: 36px; padding: 0 14px; font-size: 13px; }
.ab-btn:disabled { opacity: .5; cursor: not-allowed; }
.ab-btn.ghost { border: 1px solid #4A5561; background: transparent; color: #fff; }
.ab-btn.light { border: 1px solid #fff; background: #fff; color: var(--c-ink); }
.ab-btn.primary { border: none; background: var(--c-primary); color: #fff; font-weight: 600; padding: 0 16px; }

@media (max-width: 800px) {
  .sticky-side { position: static; }
  .review-side.sticky-side { max-height: none; overflow: visible; }
  .review-side { max-width: none; }
  .steps { margin-left: 0; }
}
</style>
