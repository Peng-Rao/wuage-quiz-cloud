<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { CHAPTERS, TYPE_ORDER, coefToDiff } from '@/data/mock'
import { bankApi, type BankQuery, type BankQuestion, type BankSort, type Page } from '@/api/bank'
import { parseApi, type KnowledgeTree, type KnowledgeTreeDetail, type KnowledgeTreeNode } from '@/api/parse'
import { useAppStore } from '@/stores/app'
import { fromBank, useBasketStore } from '@/stores/basket'
import BankQuestionCard from '@/components/bank/BankQuestionCard.vue'
import KnowledgeNav from '@/components/bank/KnowledgeNav.vue'
import DifficultyBar from '@/components/DifficultyBar.vue'

const route = useRoute()
const router = useRouter()
const basket = useBasketStore()
const { stage, subject } = storeToRefs(useAppStore())

// ---------- 左侧目录 ----------

const treeTab = ref<'章节' | '知识点'>(route.query.tree === '知识点' ? '知识点' : '章节')
watch(() => route.query.tree, (t) => { treeTab.value = t === '知识点' ? '知识点' : '章节' })
const openCh = reactive<Record<number, boolean>>({ 0: true })
const section = ref('')

const trees = ref<KnowledgeTree[]>([])
const treeId = ref<string | null>(null)
const tree = ref<KnowledgeTreeDetail | null>(null)
const counts = ref<Record<string, number>>({})
const node = ref<KnowledgeTreeNode | null>(null)
const onlyWithQuestions = ref(false)
const treeError = ref('')

/** 当前学段学科的知识树：正式导入的优先于内置，其次最新导入的（与后端 pick_tree 一致） */
async function loadTrees() {
  treeError.value = ''
  try {
    const all = await parseApi.listTrees()
    trees.value = all
      .filter((t) => t.stage === stage.value && t.subject === subject.value)
      .sort((a, b) => Number(a.builtin) - Number(b.builtin) || b.createdAt.localeCompare(a.createdAt))
  } catch (e) {
    trees.value = []
    treeError.value = (e as Error).message
  }
  treeId.value = trees.value[0]?.id ?? null
}

watch(treeId, async (id) => {
  node.value = null
  tree.value = null
  counts.value = {}
  if (!id) return
  try {
    const [t, c] = await Promise.all([parseApi.getTree(id), bankApi.knowledgeCounts(id)])
    if (treeId.value !== id) return // 已切换到其他知识树
    tree.value = t
    counts.value = c
  } catch (e) {
    if (treeId.value === id) treeError.value = (e as Error).message
  }
})
watch([stage, subject], loadTrees)

function selectNode(n: KnowledgeTreeNode | null) {
  node.value = n
}

// ---------- 筛选与题目 ----------

const THIS_YEAR = new Date().getFullYear()
const FILTERS = {
  type: { label: '题型', options: ['全部', ...TYPE_ORDER] },
  diff: { label: '难度', options: ['全部', '容易', '适中', '较难'] },
  cat: { label: '题类', options: ['全部', '高考真题', '模拟题', '期中期末', '月考', '单元测试'] },
  year: { label: '年份', options: ['全部', ...[0, 1, 2].map((i) => String(THIS_YEAR - i)), '更早'] },
} as const
type FilterKey = keyof typeof FILTERS
/** 题类 → 试卷类型关键词 */
const CAT_KEYWORDS: Record<string, string[]> = {
  高考真题: ['高考'], 模拟题: ['模拟', '联考', '质检'], 期中期末: ['期中', '期末'], 月考: ['月考'], 单元测试: ['单元', '周练', '测验'],
}
const filters = reactive<Record<FilterKey, string>>({ type: '全部', diff: '全部', cat: '全部', year: '全部' })

const SORTS: { key: BankSort; label: string }[] = [
  { key: 'default', label: '综合排序' }, { key: 'latest', label: '最新' },
  { key: 'easy', label: '由易到难' }, { key: 'hard', label: '由难到易' },
]
const sort = ref<BankSort>('default')

const keyword = computed(() => (typeof route.query.q === 'string' ? route.query.q.trim() : ''))
function clearKeyword() {
  router.replace({ query: { ...route.query, q: undefined } })
}

const PAGE_SIZE = 10
const page = ref(1)
const result = ref<Page<BankQuestion>>({ items: [], total: 0 })
const loading = ref(false)
const error = ref('')

const baseQuery = computed<BankQuery>(() => ({
  stage: stage.value,
  subject: subject.value,
  treeId: treeTab.value === '知识点' && node.value ? treeId.value ?? undefined : undefined,
  nodeId: treeTab.value === '知识点' && node.value ? node.value.id : undefined,
  type: filters.type === '全部' ? undefined : (filters.type as BankQuery['type']),
  diff: filters.diff === '全部' ? undefined : (filters.diff as BankQuery['diff']),
  paperTypes: CAT_KEYWORDS[filters.cat],
  year: filters.year === '全部' ? undefined : filters.year === '更早' ? `<${THIS_YEAR - 2}` : filters.year,
  q: keyword.value || undefined,
  sort: sort.value,
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
})
watch(page, () => {
  load()
  window.scrollTo({ top: 0, behavior: 'smooth' })
})
onMounted(() => {
  load()
  loadTrees()
})

const pageCount = computed(() => Math.max(1, Math.ceil(result.value.total / PAGE_SIZE)))
/** 页码：首页、末页与当前页前后各 1 页，其余折叠 */
const pageList = computed(() => {
  const n = pageCount.value, p = page.value
  const keep = [...new Set([1, p - 1, p, p + 1, n])].filter((x) => x >= 1 && x <= n).sort((a, b) => a - b)
  return keep.flatMap((x, i) => (i && x - keep[i - 1] > 1 ? ['…' as const, x] : [x]))
})

const current = computed(() => {
  if (treeTab.value === '知识点') return node.value?.name ?? '全部知识点'
  return `${stage.value}${subject.value} · 全部题目`
})

// 解析展开：全局开关 + 单题覆盖
const allAns = ref(false)
const ansOpen = reactive<Record<string, boolean>>({})
const showAns = (id: string) => ansOpen[id] ?? allAns.value
function toggleAllAns() {
  allAns.value = !allAns.value
  for (const k of Object.keys(ansOpen)) delete ansOpen[k]
}

function addPage() {
  basket.addMany(result.value.items.map(fromBank))
}
const pageAllIn = computed(() => result.value.items.length > 0 && result.value.items.every((q) => basket.has(q.id)))

// ---------- 右侧试题篮 ----------

const diffCount = (d: string) => basket.items.filter((x) => coefToDiff(x.q.coef) === d).length
</script>

<template>
  <main class="pick container">
    <aside class="card tree sticky-side">
      <div class="tree-tabs">
        <button
          v-for="t in (['章节', '知识点'] as const)" :key="t" class="tree-tab"
          :class="{ on: treeTab === t }" @click="treeTab = t"
        >{{ t }}</button>
      </div>

      <template v-if="treeTab === '章节'">
        <div class="tree-book-wrap">
          <div class="tree-book">人教A版（2019）必修第一册<span class="caret">▼</span></div>
          <p class="tree-tip">教材章节目录为演示，暂未参与筛选；按知识点选题可精确定位。</p>
        </div>
        <div class="tree-list">
          <div v-for="(c, i) in CHAPTERS" :key="c.name">
            <button class="chapter" :aria-expanded="!!openCh[i]" @click="openCh[i] = !openCh[i]">
              <span class="caret">{{ openCh[i] ? '▼' : '▶' }}</span><span>{{ c.name }}</span>
            </button>
            <div v-if="openCh[i]" class="sections">
              <button
                v-for="s in c.sections" :key="s" class="chip section"
                :class="{ 'is-soft': s === section }" @click="section = s"
              >{{ s }}</button>
            </div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="tree-book-wrap">
          <select v-if="trees.length > 1" v-model="treeId" class="tree-select" aria-label="知识体系">
            <option v-for="t in trees" :key="t.id" :value="t.id">{{ t.name }}{{ t.builtin ? '（内置）' : '' }}</option>
          </select>
          <div v-else-if="trees.length" class="tree-book">{{ trees[0].name }}</div>
          <label v-if="tree" class="only">
            <input v-model="onlyWithQuestions" type="checkbox"> 只看有题的知识点
          </label>
        </div>
        <div class="tree-list">
          <p v-if="treeError" class="tree-tip err">{{ treeError }}</p>
          <p v-else-if="!trees.length" class="tree-tip">
            {{ stage }}{{ subject }}还没有知识树，可在
            <RouterLink to="/upload/knowledge">知识树管理</RouterLink> 中导入。
          </p>
          <p v-else-if="!tree" class="tree-tip">加载中…</p>
          <KnowledgeNav
            v-else :nodes="tree.nodes" :counts="counts" :selected="node?.id ?? null"
            :only-with-questions="onlyWithQuestions" @select="selectNode"
          />
        </div>
      </template>
    </aside>

    <section class="results">
      <div class="card filters">
        <div v-for="(g, key) in FILTERS" :key="key" class="filter-row">
          <span class="filter-label">{{ g.label }}</span>
          <div class="filter-opts">
            <button
              v-for="o in g.options" :key="o" class="chip"
              :class="{ 'is-solid': filters[key] === o }" @click="filters[key] = o"
            >{{ o }}</button>
          </div>
        </div>
      </div>

      <div class="toolbar">
        <span>当前：<b class="cur">{{ current }}</b></span>
        <span v-if="keyword" class="kw">关键词「{{ keyword }}」<button class="btn-link" aria-label="清除关键词" @click="clearKeyword">×</button></span>
        <span>共 <b class="count">{{ result.total }}</b> 道题</span>
        <div class="sorts">
          <button v-for="s in SORTS" :key="s.key" class="sort" :class="{ on: sort === s.key }" @click="sort = s.key">{{ s.label }}</button>
        </div>
        <button class="all-ans" @click="toggleAllAns">{{ allAns ? '收起全部解析' : '展开全部解析' }}</button>
        <button class="all-ans" :disabled="!result.items.length || pageAllIn" @click="addPage">
          {{ pageAllIn ? '本页已全部加入' : '本页全部加入试题篮' }}
        </button>
      </div>

      <div :class="{ dim: loading }" class="list">
        <BankQuestionCard
          v-for="q in result.items" :key="q.id" :q="q"
          :show-answer="showAns(q.id)" :in-basket="basket.has(q.id)"
          @toggle-answer="ansOpen[q.id] = !showAns(q.id)" @toggle-basket="basket.toggle(fromBank(q))"
        />
      </div>

      <div v-if="error" class="card empty err">加载失败：{{ error }} <button class="btn-link is-primary" @click="load">重试</button></div>
      <div v-else-if="loading && !result.items.length" class="card empty">加载中…</div>
      <div v-else-if="!result.items.length" class="card empty">
        没有符合条件的题目，试试调整筛选条件。<br>
        <span class="small">题库中的题来自「试卷解析」核对后保存的校本题目，<RouterLink to="/upload">去上传试卷</RouterLink>。</span>
      </div>

      <nav v-if="pageCount > 1" class="pager" aria-label="分页">
        <button :disabled="page === 1" aria-label="上一页" @click="page--">‹</button>
        <template v-for="(p, i) in pageList" :key="i">
          <span v-if="p === '…'" class="ellipsis">…</span>
          <button v-else :class="{ on: page === p }" :aria-current="page === p ? 'page' : undefined" @click="page = p">{{ p }}</button>
        </template>
        <button :disabled="page === pageCount" aria-label="下一页" @click="page++">›</button>
      </nav>
    </section>

    <aside class="card basket sticky-side">
      <div class="basket-head">
        <span class="basket-title">试题篮</span>
        <button v-if="basket.count" class="btn-link clear" @click="basket.clear()">清空</button>
      </div>
      <div class="basket-count">
        <span class="num">{{ basket.count }}</span><span>道题</span>
        <span v-if="basket.count" class="basket-score">共 {{ basket.totalScore }} 分</span>
      </div>
      <div class="basket-types">
        <div v-for="s in basket.sections" :key="s.type" class="type-row">
          <span>{{ s.type }}</span><span><b>{{ s.items.length }}</b> 题 · {{ s.score }} 分</span>
        </div>
        <span v-if="!basket.count" class="empty-tip">还没有选题。点击题目下方的「加入试题篮」，或从<RouterLink to="/papers">试卷库</RouterLink>整卷加入。</span>
      </div>
      <div class="basket-dist">
        <span class="muted small">难度分布</span>
        <DifficultyBar :easy="diffCount('容易')" :mid="diffCount('适中')" :hard="diffCount('较难')" />
      </div>
      <RouterLink to="/paper" class="btn btn-primary gen">生成试卷</RouterLink>
      <button class="btn smart">智能补题</button>
      <RouterLink v-if="basket.count" to="/paper" class="basket-edit">调整题序与分值 ›</RouterLink>
    </aside>
  </main>
</template>

<style scoped>
.pick { width: 100%; padding-top: 20px; padding-bottom: 48px; display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; }

/* 左侧目录树 */
.tree { flex: 0 0 270px; overflow: hidden; }
.tree-tabs { display: flex; border-bottom: 1px solid var(--c-divider); }
.tree-tab {
  flex: 1; border: none; background: transparent; height: 44px; font-size: 14px;
  color: var(--c-text-3); border-bottom: 2px solid transparent;
}
.tree-tab.on { color: var(--c-primary); font-weight: 600; border-bottom-color: var(--c-primary); }
.tree-book-wrap { padding: 12px 14px; border-bottom: 1px solid var(--c-divider); display: flex; flex-direction: column; gap: 8px; }
.tree-book {
  min-height: 32px; border: 1px solid var(--c-border); border-radius: var(--r-sm); display: flex; align-items: center;
  justify-content: space-between; padding: 4px 10px; font-size: 13px; color: var(--c-text-2);
}
.tree-select {
  height: 32px; border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: 0 8px; font-size: 13px;
  color: var(--c-text-2); background: #fff; font-family: inherit;
}
.tree-tip { margin: 0; font-size: 12px; color: var(--c-text-4); line-height: 1.6; }
.tree-list .tree-tip { padding: 8px 10px; }
.tree-tip.err { color: var(--c-hard); }
.only { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-text-3); cursor: pointer; }
.caret { font-size: 10px; color: var(--c-text-4); }
.tree-list { padding: 8px 6px 14px; display: flex; flex-direction: column; max-height: calc(100vh - 230px); overflow: auto; }
.chapter {
  width: 100%; border: none; background: transparent; text-align: left; padding: 8px; font-size: 14px; color: var(--c-ink);
  display: flex; gap: 6px; align-items: flex-start; border-radius: var(--r-sm); line-height: 1.5;
}
.chapter:hover { background: var(--c-paper); }
.chapter .caret { width: 10px; padding-top: 4px; flex-shrink: 0; }
.sections { display: flex; flex-direction: column; padding-left: 18px; }
.section { text-align: left; padding: 6px 10px; }

/* 中间结果区 */
.results { flex: 999 1 480px; display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.filters { padding: 14px 18px; display: flex; flex-direction: column; gap: 10px; }
.filter-row { display: flex; gap: 12px; align-items: flex-start; }
.filter-label { width: 40px; flex-shrink: 0; font-size: 13px; color: var(--c-text-4); padding-top: 4px; }
.filter-opts { display: flex; flex-wrap: wrap; gap: 4px; }
.filter-opts .chip { border-radius: 5px; }

.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; font-size: 13px; color: var(--c-text-3); padding: 0 4px; white-space: nowrap; }
.cur { color: var(--c-ink); font-weight: 600; }
.count { color: var(--c-primary); }
.kw { color: var(--c-ink); }
.sorts { margin-left: auto; display: flex; gap: 14px; }
.sort { border: none; background: none; padding: 0; font-size: 13px; color: var(--c-text-3); }
.sort.on { color: var(--c-primary); font-weight: 600; }
.all-ans { border: 1px solid var(--c-border); background: #fff; border-radius: var(--r-sm); padding: 4px 10px; font-size: 13px; color: var(--c-text-2); }
.all-ans:disabled { color: var(--c-text-4); cursor: default; }

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

/* 右侧试题篮 */
.basket { flex: 1 0 250px; padding: 18px; display: flex; flex-direction: column; gap: 14px; }
.basket-head { display: flex; align-items: baseline; justify-content: space-between; }
.basket-title { font-size: 16px; font-weight: 600; }
.clear { font-size: 12px; color: var(--c-text-4); }
.basket-count { display: flex; align-items: baseline; gap: 6px; font-size: 13px; color: var(--c-text-3); }
.basket-count .num { font-size: 34px; font-weight: 700; color: var(--c-ink); line-height: 1; }
.basket-score { margin-left: auto; color: var(--c-primary); font-weight: 600; }
.basket-types, .basket-dist { display: flex; flex-direction: column; gap: 8px; border-top: 1px solid var(--c-divider); padding-top: 12px; }
.basket-dist { gap: 6px; }
.type-row { display: flex; justify-content: space-between; font-size: 13px; color: var(--c-text-2); }
.type-row b { color: var(--c-ink); font-weight: 600; }
.empty-tip { font-size: 13px; color: var(--c-text-4); line-height: 1.6; }
.gen { display: flex; align-items: center; justify-content: center; }
.gen:hover { text-decoration: none; color: #fff; }
.smart { height: 36px; font-size: 13px; }
.basket-edit { text-align: center; font-size: 13px; }

@media (max-width: 800px) {
  .sticky-side { position: static; }
  .tree { flex: 1 1 100%; }
  .tree-list { max-height: 280px; }
}
</style>
