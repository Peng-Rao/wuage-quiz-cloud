<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { TYPE_ORDER } from '@/data/mock'
import { bankApi, type BankQuery, type BankQuestion, type BankSort, type Page, type QuestionFacets } from '@/api/bank'
import { parseApi, type SimilarQuestion } from '@/api/parse'
import { fromBank, useBasketStore } from '@/stores/basket'
import QuestionCard from './QuestionCard.vue'
import SimilarDialog from '@/components/parse/SimilarDialog.vue'

/** 选题结果区（章节选题、知识点选题共用）：场景 / 题型 / 难度 / 更多筛选，排序、结果内搜索、题目列表与分页 */
const props = defineProps<{
  stage: string
  subject: string
  /** 目录范围：教材章节，或知识点（多选时含任一） */
  scope: { chapterId?: string; treeId?: string; nodeIds?: string[] }
}>()

const route = useRoute()
const basket = useBasketStore()

// ---------- 筛选 ----------

const EXAM = computed(() => (props.stage === '初中' ? '中考' : props.stage === '小学' ? '小升初' : '高考'))
/** 场景 → 试卷类型或名称中的关键词 */
const SCENES = computed<{ label: string; kw: string[] }[]>(() => [
  { label: '预习', kw: ['预习'] }, { label: '课堂', kw: ['课堂', '随堂', '课时'] }, { label: '作业', kw: ['作业', '练习', '周练'] },
  { label: '单元测试', kw: ['单元'] }, { label: '阶段检测', kw: ['期中', '期末', '月考', '联考', '阶段', '质检'] },
  { label: '寒暑假', kw: ['寒假', '暑假'] },
  ...(props.stage === '小学' ? [] : [
    { label: '一轮', kw: ['一轮'] }, { label: '二轮', kw: ['二轮'] }, { label: '三轮', kw: ['三轮', '冲刺'] },
  ]),
  { label: EXAM.value, kw: [EXAM.value, '模拟'] }, { label: '学业考试', kw: ['学考', '学业水平', '合格考', '会考'] },
  { label: '特殊招生', kw: ['强基', '自主招生', '竞赛', '特长'] },
])
const DIFFS = ['容易', '适中', '较难'] as const

const f = reactive({ scene: '', type: '', diff: '', year: '', region: '', grade: '', term: '' })
const facets = ref<QuestionFacets>({ regions: [], grades: [], years: [] })
watch(() => [props.stage, props.subject], async () => {
  Object.assign(f, { scene: '', year: '', region: '', grade: '', term: '' })
  try {
    facets.value = await bankApi.questionFacets(props.stage, props.subject)
  } catch {
    facets.value = { regions: [], grades: [], years: [] }
  }
}, { immediate: true })
const yearOptions = computed(() => {
  const ys = facets.value.years.map((y) => y.name)
  return ys.length > 4 ? [...ys.slice(0, 4), `<${ys[3]}`] : ys
})
const yearLabel = (y: string) => (y.startsWith('<') ? `${y.slice(1)} 年以前` : `${y} 年`)

const SORTS: { key: BankSort; label: string }[] = [
  { key: 'default', label: '综合' }, { key: 'latest', label: '最新' },
  { key: 'easy', label: '由易到难' }, { key: 'hard', label: '由难到易' },
]
const sort = ref<BankSort>('default')

// 结果内搜索；首页搜题带来的关键词作为初始值
const kwInput = ref(typeof route.query.q === 'string' ? route.query.q : '')
const keyword = ref(kwInput.value.trim())
watch(() => route.query.q, (q) => { kwInput.value = typeof q === 'string' ? q : ''; keyword.value = kwInput.value.trim() })

// ---------- 查询 ----------

const PAGE_SIZE = 10
const page = ref(1)
const result = ref<Page<BankQuestion>>({ items: [], total: 0 })
const loading = ref(false)
const error = ref('')

const baseQuery = computed<BankQuery>(() => ({
  stage: props.stage, subject: props.subject,
  chapterId: props.scope.chapterId, treeId: props.scope.treeId,
  nodeIds: props.scope.nodeIds?.length ? props.scope.nodeIds : undefined,
  type: (f.type || undefined) as BankQuery['type'], diff: (f.diff || undefined) as BankQuery['diff'],
  paperTypes: SCENES.value.find((x) => x.label === f.scene)?.kw,
  year: f.year || undefined, region: f.region || undefined, grade: f.grade || undefined,
  term: (f.term || undefined) as BankQuery['term'], q: keyword.value || undefined, sort: sort.value,
}))

let seq = 0
async function load() {
  const my = ++seq
  loading.value = true
  error.value = ''
  try {
    const r = await bankApi.listQuestions({ ...baseQuery.value, limit: PAGE_SIZE, offset: (page.value - 1) * PAGE_SIZE })
    if (my === seq) result.value = r
  } catch (e) {
    if (my === seq) error.value = (e as Error).message
  } finally {
    if (my === seq) loading.value = false
  }
}
watch(() => JSON.stringify(baseQuery.value), () => {
  if (page.value !== 1) page.value = 1 // 由 page 的监听重新加载
  else load()
}, { immediate: true })
watch(page, () => {
  load()
  window.scrollTo({ top: 0, behavior: 'smooth' })
})

const pageCount = computed(() => Math.max(1, Math.ceil(result.value.total / PAGE_SIZE)))
const pageList = computed(() => {
  const n = pageCount.value, p = page.value
  const keep = [...new Set([1, p - 1, p, p + 1, n])].filter((x) => x >= 1 && x <= n).sort((a, b) => a - b)
  return keep.flatMap((x, i) => (i && x - keep[i - 1] > 1 ? ['…' as const, x] : [x]))
})

// 详情（答案解析）：全局开关 + 单题覆盖
const allAns = ref(false)
const ansOpen = reactive<Record<string, boolean>>({})
const showAns = (id: string) => ansOpen[id] ?? allAns.value
function toggleAllAns() {
  allAns.value = !allAns.value
  for (const k of Object.keys(ansOpen)) delete ansOpen[k]
}

const pageAllIn = computed(() => result.value.items.length > 0 && result.value.items.every((q) => basket.has(q.id)))

// 相似题
const simOpen = ref(false)
const simTitle = ref('')
const simItems = ref<SimilarQuestion[] | null>(null)
const simError = ref('')
async function openSimilar(q: BankQuestion, no: number) {
  simTitle.value = `第 ${no} 题 · 相似题`
  simItems.value = null
  simError.value = ''
  simOpen.value = true
  try {
    const got = await parseApi.searchSimilar(`${q.stem}\n${q.options.join('\n')}`, { scope: 'bank', type: q.type, limit: 9 })
    simItems.value = got.filter((x) => x.id !== q.id).slice(0, 8)
  } catch (e) {
    simError.value = (e as Error).message
  }
}

const hasFilter = computed(() => Object.values(f).some(Boolean) || !!keyword.value)
function resetFilters() {
  Object.assign(f, { scene: '', type: '', diff: '', year: '', region: '', grade: '', term: '' })
  kwInput.value = ''
  keyword.value = ''
}
</script>

<template>
  <div class="qb">
    <div class="card filters">
      <div class="row">
        <span class="label">场景</span>
        <div class="opts">
          <button class="opt" :class="{ on: !f.scene }" @click="f.scene = ''">全部</button>
          <button v-for="s in SCENES" :key="s.label" class="opt" :class="{ on: f.scene === s.label }" @click="f.scene = s.label">{{ s.label }}</button>
        </div>
      </div>
      <div class="row">
        <span class="label">题型</span>
        <div class="opts">
          <button class="opt" :class="{ on: !f.type }" @click="f.type = ''">全部</button>
          <button v-for="t in TYPE_ORDER" :key="t" class="opt" :class="{ on: f.type === t }" @click="f.type = t">{{ t }}</button>
        </div>
      </div>
      <div class="row">
        <span class="label">难度</span>
        <div class="opts">
          <button class="opt" :class="{ on: !f.diff }" @click="f.diff = ''">全部</button>
          <button v-for="d in DIFFS" :key="d" class="opt" :class="{ on: f.diff === d }" @click="f.diff = d">{{ d }}</button>
        </div>
      </div>
      <div class="row more">
        <span class="label">更多</span>
        <div class="opts">
          <label class="sel" :class="{ on: f.year }">
            <select v-model="f.year" aria-label="年份">
              <option value="">年份</option>
              <option v-for="y in yearOptions" :key="y" :value="y">{{ yearLabel(y) }}</option>
            </select>
          </label>
          <label class="sel" :class="{ on: f.region }">
            <select v-model="f.region" aria-label="地区">
              <option value="">地区</option>
              <option v-for="r in facets.regions" :key="r.name" :value="r.name">{{ r.name }}（{{ r.count }}）</option>
            </select>
          </label>
          <label class="sel" :class="{ on: f.grade }">
            <select v-model="f.grade" aria-label="年级">
              <option value="">年级</option>
              <option v-for="g in facets.grades" :key="g.name" :value="g.name">{{ g.name }}（{{ g.count }}）</option>
            </select>
          </label>
          <label class="sel" :class="{ on: f.term }">
            <select v-model="f.term" aria-label="学期">
              <option value="">学期</option>
              <option value="上">上学期</option>
              <option value="下">下学期</option>
            </select>
          </label>
          <button v-if="hasFilter" class="reset" @click="resetFilters">清空筛选</button>
        </div>
      </div>
    </div>

    <div class="card sortbar">
      <div class="sorts">
        <button v-for="s in SORTS" :key="s.key" class="sort" :class="{ on: sort === s.key }" @click="sort = s.key">{{ s.label }}</button>
      </div>
      <form class="search" role="search" @submit.prevent="keyword = kwInput.trim()">
        <input v-model="kwInput" placeholder="在结果中搜索" aria-label="在结果中搜索" @blur="keyword = kwInput.trim()">
      </form>
      <button class="tool" @click="toggleAllAns">{{ allAns ? '收起全部详情' : '展开全部详情' }}</button>
      <button class="tool" :disabled="!result.items.length || pageAllIn" @click="basket.addMany(result.items.map(fromBank))">
        {{ pageAllIn ? '本页已全部加入' : '本页全部加入' }}
      </button>
      <span class="total">共计 <b>{{ result.total }}</b> 道试题</span>
    </div>

    <div class="list" :class="{ dim: loading }">
      <QuestionCard
        v-for="(q, i) in result.items" :key="q.id" :q="q" :no="(page - 1) * PAGE_SIZE + i + 1"
        :show-answer="showAns(q.id)" :in-basket="basket.has(q.id)"
        @toggle-answer="ansOpen[q.id] = !showAns(q.id)" @toggle-basket="basket.toggle(fromBank(q))"
        :can-similar="auth.isStaff" @similar="openSimilar(q, (page - 1) * PAGE_SIZE + i + 1)"
      />
    </div>

    <div v-if="error" class="card empty err">加载失败：{{ error }} <button class="btn-link is-primary" @click="load">重试</button></div>
    <div v-else-if="loading && !result.items.length" class="card empty">加载中…</div>
    <div v-else-if="!result.items.length" class="card empty">
      没有符合条件的题目，试试调整目录或筛选条件。<br>
      <span v-if="auth.isStaff" class="small">题库中的题来自「试卷解析」核对后保存的校本题目，<RouterLink to="/upload">去上传试卷</RouterLink>。</span>
    </div>

    <nav v-if="pageCount > 1" class="pager" aria-label="分页">
      <button :disabled="page === 1" aria-label="上一页" @click="page--">‹</button>
      <template v-for="(p, i) in pageList" :key="i">
        <span v-if="p === '…'" class="ellipsis">…</span>
        <button v-else :class="{ on: page === p }" :aria-current="page === p ? 'page' : undefined" @click="page = p">{{ p }}</button>
      </template>
      <button :disabled="page === pageCount" aria-label="下一页" @click="page++">›</button>
    </nav>

    <SimilarDialog v-model="simOpen" :title="simTitle" :items="simItems" :error="simError" />
  </div>
</template>

<style scoped>
.qb { display: flex; flex-direction: column; gap: 14px; min-width: 0; }

.filters { padding: 8px 22px; }
.row { display: flex; gap: 12px; align-items: flex-start; padding: 8px 0; }
.row.more { border-top: 1px dashed var(--c-divider); margin-top: 4px; padding-top: 12px; }
.label { width: 40px; flex-shrink: 0; font-size: 14px; color: var(--c-text-4); line-height: 30px; }
.opts { display: flex; flex-wrap: wrap; gap: 2px 6px; align-items: center; }
.opt { border: none; background: none; padding: 4px 10px; font-size: 14px; line-height: 22px; color: var(--c-text-2); border-radius: var(--r-sm); }
.opt:hover { color: var(--c-primary); }
.opt.on { color: var(--c-primary); font-weight: 600; background: var(--c-primary-soft); }
.sel { position: relative; display: inline-flex; margin-right: 8px; }
.sel::after { content: '▾'; position: absolute; right: 8px; top: 50%; transform: translateY(-50%); font-size: 10px; color: var(--c-text-4); pointer-events: none; }
.sel select {
  appearance: none; border: 1px solid transparent; background: none; font: inherit; font-size: 14px; color: var(--c-text-2);
  padding: 4px 24px 4px 10px; border-radius: var(--r-sm); cursor: pointer;
}
.sel select:hover { color: var(--c-primary); }
.sel select:focus-visible { outline: none; border-color: var(--c-primary); }
.sel.on select { color: var(--c-primary); font-weight: 600; background: var(--c-primary-soft); }
.reset { border: none; background: none; font-size: 13px; color: var(--c-text-4); padding: 4px 8px; }
.reset:hover { color: var(--c-primary); }

.sortbar { padding: 10px 22px; display: flex; flex-wrap: wrap; align-items: center; gap: 10px 18px; }
.sorts { display: flex; gap: 18px; }
.sort { border: none; background: none; padding: 0; font-size: 15px; color: var(--c-text-2); }
.sort.on { color: var(--c-primary); font-weight: 600; }
.search { flex: 1 1 200px; max-width: 280px; margin-left: auto; }
.search input {
  width: 100%; height: 32px; border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: 0 10px 0 30px; font-size: 13px;
  background: #fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' fill='none' stroke='%238A8F95' stroke-width='2'%3E%3Ccircle cx='6' cy='6' r='4.5'/%3E%3Cpath d='m9.5 9.5 3 3'/%3E%3C/svg%3E") no-repeat 10px center;
}
.search input:focus { outline: none; border-color: var(--c-primary); }
.tool { border: 1px solid var(--c-border); background: #fff; border-radius: var(--r-sm); padding: 4px 10px; font-size: 13px; color: var(--c-text-2); }
.tool:disabled { color: var(--c-text-4); cursor: default; }
.total { font-size: 14px; color: var(--c-text-2); white-space: nowrap; }
.total b { color: var(--c-primary); font-weight: 600; }

.list { display: flex; flex-direction: column; gap: 14px; transition: opacity .15s; }
.list.dim { opacity: .55; }
.empty { padding: 40px; text-align: center; color: var(--c-text-4); font-size: 14px; line-height: 1.9; }
.empty.err { color: var(--c-hard); }
.small { font-size: 12px; }

.pager { display: flex; justify-content: center; gap: 6px; padding: 8px 0; }
.pager button {
  min-width: 32px; height: 32px; border-radius: var(--r-sm); background: #fff; border: 1px solid var(--c-border);
  font-size: 13px; color: var(--c-ink);
}
.pager button:disabled { color: var(--c-text-4); cursor: default; }
.pager button.on { background: var(--c-primary); border-color: var(--c-primary); color: #fff; }
.ellipsis { padding: 0 6px; display: flex; align-items: center; color: var(--c-text-4); }

@media (max-width: 640px) {
  .filters, .sortbar { padding-left: 14px; padding-right: 14px; }
  .search { max-width: none; margin-left: 0; }
}
</style>
