<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { TYPE_ORDER, coefToDiff } from '@/data/mock'
import { bankApi, type FacetCount, type PaperFacets, type PaperPage, type PaperSummary } from '@/api/bank'
import { fromBank, useBasketStore } from '@/stores/basket'

const route = useRoute()
const router = useRouter()
const basket = useBasketStore()

type Dim = 'stage' | 'grade' | 'subject' | 'paperType'
const DIMS: { key: Dim; label: string; facet: keyof PaperFacets }[] = [
  { key: 'stage', label: '学段', facet: 'stages' },
  { key: 'grade', label: '年级', facet: 'grades' },
  { key: 'subject', label: '学科', facet: 'subjects' },
  { key: 'paperType', label: '类型', facet: 'paperTypes' },
]

// 筛选条件保存在地址栏，返回、刷新后保持
const str = (v: unknown) => (typeof v === 'string' ? v : '')
const filters = computed(() => ({
  stage: str(route.query.stage), grade: str(route.query.grade), subject: str(route.query.subject),
  paperType: str(route.query.paperType), q: str(route.query.q),
}))
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const keyword = ref(filters.value.q)
watch(() => filters.value.q, (q) => { keyword.value = q })

function setQuery(patch: Record<string, string | number | undefined>) {
  const next: Record<string, string> = {}
  for (const [k, v] of Object.entries({ ...route.query, page: undefined, ...patch })) if (v) next[k] = String(v)
  router.replace({ query: next })
}
function pick(dim: Dim, value: string) {
  // 换学段后原年级可能不属于该学段，一并清除
  setQuery(dim === 'stage' ? { stage: value, grade: undefined } : { [dim]: value })
}

const PAGE_SIZE = 15
const data = ref<PaperPage | null>(null)
const loading = ref(false)
const error = ref('')
let seq = 0
async function load() {
  const my = ++seq
  loading.value = true
  error.value = ''
  try {
    const f = filters.value
    const r = await bankApi.listPapers({
      stage: f.stage || undefined, grade: f.grade || undefined, subject: f.subject || undefined,
      paperType: f.paperType || undefined, q: f.q || undefined, limit: PAGE_SIZE, offset: (page.value - 1) * PAGE_SIZE,
    })
    if (my === seq) data.value = r
  } catch (e) {
    if (my === seq) error.value = (e as Error).message
  } finally {
    if (my === seq) loading.value = false
  }
}
watch(() => route.query, load, { immediate: true, deep: true })

const facetOf = (d: (typeof DIMS)[number]): FacetCount[] => data.value?.facets[d.facet] ?? []
const pageCount = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / PAGE_SIZE)))

const typeSummary = (p: PaperSummary) =>
  TYPE_ORDER.filter((t) => p.typeCounts[t]).map((t) => `${t.replace('题', '')} ${p.typeCounts[t]}`).join(' · ')
const tagsOf = (p: PaperSummary) =>
  [p.meta.stage && p.meta.grade ? p.meta.grade : p.meta.stage, p.meta.subject, p.meta.region, p.meta.schoolYear].filter(Boolean)

// 整卷加入试题篮
const adding = reactive<Record<string, 'loading' | 'done'>>({})
async function addPaper(p: PaperSummary) {
  adding[p.id] = 'loading'
  try {
    const d = await bankApi.getPaper(p.id)
    basket.addMany(d.questions.map(fromBank))
    adding[p.id] = 'done'
  } catch (e) {
    delete adding[p.id]
    error.value = (e as Error).message
  }
}
</script>

<template>
  <main class="papers container">
    <div class="page-head">
      <div>
        <h1>试卷库</h1>
        <p>按年级、学科浏览已入库的整套试卷；可整卷加入试题篮，或直接以原卷组卷。</p>
      </div>
      <form class="search" @submit.prevent="setQuery({ q: keyword.trim() || undefined })">
        <input v-model="keyword" placeholder="试卷名称、地区、学年" aria-label="搜索试卷">
        <button type="submit">搜索</button>
      </form>
    </div>

    <div class="card filters">
      <div v-for="d in DIMS" :key="d.key" class="filter-row">
        <span class="filter-label">{{ d.label }}</span>
        <div class="filter-opts">
          <button class="chip" :class="{ 'is-solid': !filters[d.key] }" @click="pick(d.key, '')">全部</button>
          <button
            v-for="f in facetOf(d)" :key="f.name" class="chip"
            :class="{ 'is-solid': filters[d.key] === f.name }" @click="pick(d.key, f.name)"
          >{{ f.name }}<span class="n">{{ f.count }}</span></button>
          <!-- 当前选中但已无试卷的值也显示，便于取消 -->
          <button
            v-if="filters[d.key] && !facetOf(d).some((f) => f.name === filters[d.key])"
            class="chip is-solid" @click="pick(d.key, '')"
          >{{ filters[d.key] }}<span class="n">0</span></button>
        </div>
      </div>
    </div>

    <div class="toolbar">
      <span>共 <b class="count">{{ data?.total ?? 0 }}</b> 套试卷</span>
      <span v-if="filters.q" class="kw">关键词「{{ filters.q }}」<button class="btn-link" aria-label="清除关键词" @click="setQuery({ q: undefined })">×</button></span>
      <RouterLink v-if="basket.count" to="/paper" class="to-basket">试题篮 {{ basket.count }} 题 · 去组卷 ›</RouterLink>
    </div>

    <div v-if="error" class="card empty err">加载失败：{{ error }} <button class="btn-link is-primary" @click="load">重试</button></div>
    <div v-else-if="!data && loading" class="card empty">加载中…</div>
    <div v-else-if="data && !data.items.length" class="card empty">
      没有符合条件的试卷。<br>
      <span class="small">试卷库收录「试卷解析」中核对并保存到校本题库的试卷，<RouterLink to="/upload">去上传试卷</RouterLink>。</span>
    </div>

    <div v-else class="card list" :class="{ dim: loading }">
      <div v-for="p in data?.items" :key="p.id" class="paper-row">
        <span class="tag">{{ p.meta.paperType || '试卷' }}</span>
        <div class="main">
          <RouterLink :to="`/papers/${p.id}`" class="title">{{ p.title }}</RouterLink>
          <div class="meta">
            <span v-for="t in tagsOf(p)" :key="t">{{ t }}</span>
          </div>
          <div class="stats">
            <span>{{ p.questionCount }} 题</span>
            <span v-if="p.sourceQuestionCount > p.questionCount" class="partial">原卷 {{ p.sourceQuestionCount }} 题，部分入库</span>
            <span>总分 {{ p.totalScore }}</span>
            <span v-if="p.avgCoef !== null">难度 {{ coefToDiff(p.avgCoef) }}（{{ p.avgCoef.toFixed(2) }}）</span>
            <span class="types">{{ typeSummary(p) }}</span>
          </div>
        </div>
        <div class="ops">
          <span class="date">{{ p.updatedAt.slice(0, 10) }}</span>
          <div class="btns">
            <RouterLink :to="`/papers/${p.id}`" class="op">查看</RouterLink>
            <button class="op primary" :disabled="!!adding[p.id]" @click="addPaper(p)">
              {{ adding[p.id] === 'done' ? '已加入试题篮' : adding[p.id] === 'loading' ? '加入中…' : '整卷加入试题篮' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <nav v-if="pageCount > 1" class="pager" aria-label="分页">
      <button
        v-for="n in pageCount" :key="n" :class="{ on: page === n }" :aria-current="page === n ? 'page' : undefined"
        @click="setQuery({ page: n > 1 ? n : undefined })"
      >{{ n }}</button>
    </nav>
  </main>
</template>

<style scoped>
.papers { padding-top: 24px; padding-bottom: 56px; display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; flex-wrap: wrap; gap: 16px 32px; align-items: flex-end; justify-content: space-between; }
.page-head h1 { margin: 0 0 6px; font-size: 22px; font-weight: 700; }
.page-head p { margin: 0; font-size: 13px; color: var(--c-text-3); }
.search { display: flex; gap: 6px; width: min(100%, 400px); }
.search input {
  flex: 1; min-width: 0; height: 36px; border: 1px solid var(--c-border); border-radius: var(--r-md); padding: 0 12px;
  font-size: 14px; background: #fff; color: var(--c-ink);
}
.search input:focus { outline: none; border-color: var(--c-primary); }
.search button { border: none; background: var(--c-ink); color: #fff; border-radius: var(--r-md); padding: 0 18px; font-size: 14px; }

.filters { padding: 14px 18px; display: flex; flex-direction: column; gap: 10px; }
.filter-row { display: flex; gap: 12px; align-items: flex-start; }
.filter-label { width: 40px; flex-shrink: 0; font-size: 13px; color: var(--c-text-4); padding-top: 4px; }
.filter-opts { display: flex; flex-wrap: wrap; gap: 4px; }
.filter-opts .chip { border-radius: 5px; }
.n { margin-left: 4px; font-size: 11px; opacity: .65; }

.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; font-size: 13px; color: var(--c-text-3); padding: 0 4px; }
.count { color: var(--c-primary); }
.kw { color: var(--c-ink); }
.to-basket { margin-left: auto; }

.empty { padding: 40px; text-align: center; color: var(--c-text-4); font-size: 14px; line-height: 1.9; }
.empty.err { color: var(--c-hard); }
.small { font-size: 12px; }

.list { padding: 4px 22px; transition: opacity .15s; }
.list.dim { opacity: .55; }
.paper-row { display: flex; align-items: flex-start; gap: 14px; padding: 16px 0; border-bottom: 1px dashed var(--c-divider); }
.paper-row:last-child { border-bottom: none; }
.paper-row .tag { flex-shrink: 0; margin-top: 2px; padding: 2px 6px; }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.title { font-size: 15px; font-weight: 500; color: var(--c-ink); line-height: 1.5; }
.title:hover { color: var(--c-primary); }
.meta, .stats { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px; color: var(--c-text-4); }
.stats { color: var(--c-text-3); }
.partial { color: #B5661B; }
.ops { flex-shrink: 0; display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
.date { font-size: 12px; color: var(--c-text-4); }
.btns { display: flex; gap: 8px; }
.op {
  height: 30px; padding: 0 12px; border-radius: var(--r-sm); border: 1px solid var(--c-border); background: #fff;
  font-size: 13px; color: var(--c-text-2); display: inline-flex; align-items: center;
}
.op:hover { text-decoration: none; border-color: var(--c-primary); color: var(--c-primary); }
.op.primary { border-color: var(--c-primary); color: var(--c-primary); }
.op.primary:hover:not(:disabled) { background: var(--c-primary-soft); }
.op:disabled { color: var(--c-text-4); border-color: var(--c-border); cursor: default; }

.pager { display: flex; justify-content: center; flex-wrap: wrap; gap: 6px; padding: 8px 0; }
.pager button {
  min-width: 32px; height: 32px; border-radius: var(--r-sm); background: #fff; border: 1px solid var(--c-border);
  font-size: 13px; color: var(--c-ink);
}
.pager button.on { background: var(--c-primary); border-color: var(--c-primary); color: #fff; }

@media (max-width: 640px) {
  .list { padding: 4px 14px; }
  .paper-row { flex-wrap: wrap; }
  .ops { flex-direction: row; align-items: center; width: 100%; justify-content: space-between; }
}
</style>
