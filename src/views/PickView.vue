<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CHAPTERS, FILTERS, QUESTIONS, TYPE_ORDER, type FilterKey } from '@/data/mock'
import { useBasketStore } from '@/stores/basket'
import DifficultyBar from '@/components/DifficultyBar.vue'

const route = useRoute()
const router = useRouter()
const basket = useBasketStore()

const treeTab = ref<'章节' | '知识点'>(route.query.tree === '知识点' ? '知识点' : '章节')
const treeBook = computed(() => (treeTab.value === '章节' ? '人教A版（2019）必修第一册' : '高中数学知识体系'))
const openCh = reactive<Record<number, boolean>>({ 0: true })
const section = ref('1.2 集合间的基本关系')

const filters = reactive<Record<FilterKey, string>>({ type: '全部', diff: '全部', cat: '全部', year: '全部' })
const SORTS = ['综合排序', '最新', '组卷次数'] as const
const sort = ref<(typeof SORTS)[number]>('综合排序')
const page = ref(1)

const keyword = computed(() => (typeof route.query.q === 'string' ? route.query.q : ''))
function clearKeyword() {
  router.replace({ query: { ...route.query, q: undefined } })
}

const list = computed(() => {
  const kw = keyword.value
  const out = QUESTIONS.filter((q) =>
    (filters.type === '全部' || q.type === filters.type)
    && (filters.diff === '全部' || q.diff === filters.diff)
    && (!kw || q.stem.includes(kw) || q.knowledge.includes(kw)),
  )
  if (sort.value === '最新') return [...out].sort((a, b) => b.date.localeCompare(a.date))
  if (sort.value === '组卷次数') return [...out].sort((a, b) => b.uses - a.uses)
  return out
})
// 演示：按筛选结果估算题库总量
const resultCount = computed(() => {
  if (keyword.value) return list.value.length
  return list.value.length ? 1286 - (QUESTIONS.length - list.value.length) * 173 : 0
})

// 解析展开：全局开关 + 单题覆盖
const allAns = ref(false)
const ansOpen = reactive<Record<number, boolean>>({})
const showAns = (id: number) => ansOpen[id] ?? allAns.value
function toggleAllAns() {
  allAns.value = !allAns.value
  for (const k of Object.keys(ansOpen)) delete ansOpen[+k]
}

watch([filters, section, keyword], () => { page.value = 1 })

const basketTypes = computed(() =>
  TYPE_ORDER.map((t) => ({ type: t, n: basket.questions.filter((q) => q.type === t).length })).filter((x) => x.n),
)
const diffCount = (d: string) => basket.questions.filter((q) => q.diff === d).length
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
      <div class="tree-book-wrap">
        <div class="tree-book">{{ treeBook }}<span class="caret">▼</span></div>
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
    </aside>

    <section class="results">
      <div class="card filters">
        <div v-for="g in FILTERS" :key="g.key" class="filter-row">
          <span class="filter-label">{{ g.label }}</span>
          <div class="filter-opts">
            <button
              v-for="o in g.options" :key="o" class="chip"
              :class="{ 'is-solid': filters[g.key] === o }" @click="filters[g.key] = o"
            >{{ o }}</button>
          </div>
        </div>
      </div>

      <div class="toolbar">
        <span>当前：<b class="cur">{{ section }}</b></span>
        <span v-if="keyword" class="kw">关键词「{{ keyword }}」<button class="btn-link" @click="clearKeyword">×</button></span>
        <span>共 <b class="count">{{ resultCount }}</b> 道题</span>
        <div class="sorts">
          <button v-for="s in SORTS" :key="s" class="sort" :class="{ on: sort === s }" @click="sort = s">{{ s }}</button>
        </div>
        <button class="all-ans" @click="toggleAllAns">{{ allAns ? '收起全部解析' : '展开全部解析' }}</button>
      </div>

      <article
        v-for="q in list" :key="q.id" class="card question"
        :class="{ 'in-basket': basket.has(q.id) }"
      >
        <div class="q-body">
          <div class="q-meta">
            <span class="tag">{{ q.type }}</span>
            <span>{{ q.source }}</span>
          </div>
          <div class="q-stem serif">{{ q.stem }}</div>
          <div v-if="q.options.length" class="q-options serif">
            <span v-for="o in q.options" :key="o">{{ o }}</span>
          </div>
        </div>
        <div v-if="showAns(q.id)" class="q-answer">
          <div><b>【答案】</b>{{ q.answer }}</div>
          <div><b>【解析】</b>{{ q.analysis }}</div>
          <div class="kp"><b>【知识点】</b>{{ q.knowledge }}</div>
        </div>
        <div class="q-foot">
          <span>难度 <b>{{ q.diff }}</b></span>
          <span>组卷 {{ q.uses }} 次</span>
          <span>更新 {{ q.date }}</span>
          <div class="q-actions">
            <button class="btn-link is-primary" @click="ansOpen[q.id] = !showAns(q.id)">
              {{ showAns(q.id) ? '收起解析' : '查看解析' }}
            </button>
            <button class="btn-link">收藏</button>
            <button class="btn-link">纠错</button>
            <button class="add-btn" :class="{ added: basket.has(q.id) }" @click="basket.toggle(q.id)">
              {{ basket.has(q.id) ? '移出试题篮' : '＋ 加入试题篮' }}
            </button>
          </div>
        </div>
      </article>

      <div v-if="!list.length" class="card empty">没有符合条件的题目，试试调整筛选条件。</div>

      <nav v-else class="pager" aria-label="分页">
        <button v-for="p in [1, 2, 3]" :key="p" :class="{ on: page === p }" @click="page = p">{{ p }}</button>
        <span class="ellipsis">…</span>
        <button class="wide" :class="{ on: page === 86 }" @click="page = 86">86</button>
      </nav>
    </section>

    <aside class="card basket sticky-side">
      <div class="basket-head">
        <span class="basket-title">试题篮</span>
        <button class="btn-link clear" @click="basket.clear()">清空</button>
      </div>
      <div class="basket-count"><span class="num">{{ basket.count }}</span><span>道题</span></div>
      <div class="basket-types">
        <div v-for="b in basketTypes" :key="b.type" class="type-row">
          <span>{{ b.type }}</span><b>{{ b.n }}</b>
        </div>
        <span v-if="!basket.count" class="empty-tip">还没有选题。点击题目下方的「加入试题篮」。</span>
      </div>
      <div class="basket-dist">
        <span class="muted small">难度分布</span>
        <DifficultyBar :easy="diffCount('容易')" :mid="diffCount('适中')" :hard="diffCount('较难')" />
      </div>
      <RouterLink to="/paper" class="btn btn-primary gen">生成试卷</RouterLink>
      <button class="btn smart">智能补题</button>
    </aside>
  </main>
</template>

<style scoped>
.pick { width: 100%; padding-top: 20px; padding-bottom: 48px; display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; }

/* 左侧目录树 */
.tree { flex: 0 0 250px; overflow: hidden; }
.tree-tabs { display: flex; border-bottom: 1px solid var(--c-divider); }
.tree-tab {
  flex: 1; border: none; background: transparent; height: 44px; font-size: 14px;
  color: var(--c-text-3); border-bottom: 2px solid transparent;
}
.tree-tab.on { color: var(--c-primary); font-weight: 600; border-bottom-color: var(--c-primary); }
.tree-book-wrap { padding: 12px 14px; border-bottom: 1px solid var(--c-divider); }
.tree-book {
  height: 32px; border: 1px solid var(--c-border); border-radius: var(--r-sm); display: flex; align-items: center;
  justify-content: space-between; padding: 0 10px; font-size: 13px; color: var(--c-text-2);
}
.caret { font-size: 10px; color: var(--c-text-4); }
.tree-list { padding: 8px 6px 14px; display: flex; flex-direction: column; max-height: calc(100vh - 220px); overflow: auto; }
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

.question { overflow: hidden; transition: border-color .15s; }
.question.in-basket { border-color: var(--c-primary-line); }
.q-body { padding: 18px 22px 14px; display: flex; flex-direction: column; gap: 12px; }
.q-meta { display: flex; gap: 8px; font-size: 12px; color: var(--c-text-4); align-items: center; }
.q-stem { font-size: 15.5px; line-height: 1.9; color: var(--c-ink); text-wrap: pretty; }
.q-options { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 4px 20px; font-size: 15px; line-height: 1.9; }
.q-answer {
  margin: 0 22px 14px; padding: 12px 16px; background: var(--c-paper); border-radius: var(--r-md);
  display: flex; flex-direction: column; gap: 6px; font-size: 14px; line-height: 1.8;
}
.q-answer b { color: var(--c-primary); font-weight: 600; }
.q-answer .kp { color: var(--c-text-3); }
.q-foot {
  border-top: 1px solid var(--c-divider); padding: 10px 22px; display: flex; flex-wrap: wrap; align-items: center;
  gap: 8px 18px; font-size: 12px; color: var(--c-text-4); background: var(--c-surface-2);
}
.q-foot b { color: var(--c-text-2); font-weight: 500; }
.q-actions { margin-left: auto; display: flex; gap: 14px; align-items: center; }
.add-btn { border: none; border-radius: var(--r-sm); padding: 6px 14px; font-size: 13px; background: var(--c-primary); color: #fff; }
.add-btn.added { background: var(--c-paper); color: var(--c-text-3); }

.empty { padding: 40px; text-align: center; color: var(--c-text-4); font-size: 14px; }

.pager { display: flex; justify-content: center; gap: 6px; padding: 8px 0; }
.pager button {
  min-width: 32px; height: 32px; border-radius: var(--r-sm); background: #fff; border: 1px solid var(--c-border);
  font-size: 13px; color: var(--c-ink);
}
.pager button.wide { min-width: 40px; }
.pager button.on { background: var(--c-primary); border-color: var(--c-primary); color: #fff; }
.ellipsis { padding: 0 6px; display: flex; align-items: center; color: var(--c-text-4); }

/* 右侧试题篮 */
.basket { flex: 1 0 250px; padding: 18px; display: flex; flex-direction: column; gap: 14px; }
.basket-head { display: flex; align-items: baseline; justify-content: space-between; }
.basket-title { font-size: 16px; font-weight: 600; }
.clear { font-size: 12px; color: var(--c-text-4); }
.basket-count { display: flex; align-items: baseline; gap: 6px; font-size: 13px; color: var(--c-text-3); }
.basket-count .num { font-size: 34px; font-weight: 700; color: var(--c-ink); line-height: 1; }
.basket-types, .basket-dist { display: flex; flex-direction: column; gap: 8px; border-top: 1px solid var(--c-divider); padding-top: 12px; }
.basket-dist { gap: 6px; }
.small { font-size: 12px; }
.type-row { display: flex; justify-content: space-between; font-size: 13px; color: var(--c-text-2); }
.type-row b { color: var(--c-ink); font-weight: 600; }
.empty-tip { font-size: 13px; color: var(--c-text-4); line-height: 1.6; }
.gen { display: flex; align-items: center; justify-content: center; }
.gen:hover { text-decoration: none; color: #fff; }
.smart { height: 36px; font-size: 13px; }

@media (max-width: 800px) {
  .sticky-side { position: static; }
  .tree { flex: 1 1 100%; }
  .tree-list { max-height: 280px; }
}
</style>
