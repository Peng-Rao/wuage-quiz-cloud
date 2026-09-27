<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { bankApi, type PaperSummary, type TextbookBook, type TextbookVersion } from '@/api/bank'
import { useAppStore } from '@/stores/app'
import { fromBank, useBasketStore } from '@/stores/basket'
import QuestionBrowser from '@/components/bank/QuestionBrowser.vue'
import TreeNav, { type TreeItem } from '@/components/bank/TreeNav.vue'
import BasketFloat from '@/components/bank/BasketFloat.vue'

const route = useRoute()
const router = useRouter()
const basket = useBasketStore()
const { stage, subject } = storeToRefs(useAppStore())

// ---------- 教材版本与册 ----------

const versions = ref<TextbookVersion[]>([])
const loadError = ref('')
const loaded = ref(false)
const version = ref<TextbookVersion | null>(null)
const book = ref<TextbookBook | null>(null)
const counts = ref<Record<string, number>>({})

/** 上次选择的册按学段学科记住 */
const bookKey = () => `fg-book:${stage.value}${subject.value}`
function remembered(): string | null {
  try { return localStorage.getItem(bookKey()) } catch { return null }
}

async function loadCatalog() {
  loaded.value = false
  loadError.value = ''
  try {
    versions.value = await bankApi.chapters(stage.value, subject.value)
  } catch (e) {
    versions.value = []
    loadError.value = (e as Error).message
  }
  const want = (typeof route.query.book === 'string' && route.query.book) || remembered()
  const all = versions.value.flatMap((v) => v.books.map((b) => ({ v, b })))
  const hit = all.find((x) => x.b.id === want) ?? all[0]
  version.value = hit?.v ?? null
  pickBook(hit?.b ?? null, false)
  loaded.value = true
}
watch([stage, subject], loadCatalog, { immediate: true })

function pickBook(b: TextbookBook | null, close = true) {
  book.value = b
  chapterId.value = null
  counts.value = {}
  if (close) pickerOpen.value = false
  if (!b) return
  try { localStorage.setItem(bookKey(), b.id) } catch { /* 无法写入时忽略 */ }
  if (route.query.book !== b.id) router.replace({ query: { ...route.query, book: b.id } })
  bankApi.chapterCounts(b.id).then((c) => { if (book.value?.id === b.id) counts.value = c }).catch(() => {})
}
function pickVersion(v: TextbookVersion) {
  version.value = v
  pickBook(v.books[0] ?? null, false)
}

// 教材选择面板
const pickerOpen = ref(false)
const picker = ref<HTMLElement>()
function onDocClick(e: MouseEvent) {
  if (pickerOpen.value && picker.value && !picker.value.contains(e.target as Node)) pickerOpen.value = false
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

// ---------- 章节目录 ----------

const tree = computed<TreeItem[]>(() => book.value?.chapters.map((c) => ({
  id: c.id, name: c.name, children: c.sections.map((x) => ({ id: x.id, name: x.name })),
})) ?? [])
const chapterId = ref<string | null>(null)
const onlyWithQuestions = ref(false)
const currentName = computed(() => {
  if (!chapterId.value) return '全部章节'
  for (const c of book.value?.chapters ?? []) {
    if (c.id === chapterId.value) return c.name
    const x = c.sections.find((s) => s.id === chapterId.value)
    if (x) return x.name
  }
  return ''
})

// ---------- 同步套卷 ----------

const tab = ref<'questions' | 'papers'>('questions')
const papers = ref<PaperSummary[] | null>(null)
const papersError = ref('')
watch([tab, stage, subject], async () => {
  if (tab.value !== 'papers') return
  papers.value = null
  papersError.value = ''
  try {
    papers.value = (await bankApi.listPapers({ stage: stage.value, subject: subject.value, limit: 50 })).items
  } catch (e) {
    papersError.value = (e as Error).message
  }
})
async function addPaper(p: PaperSummary) {
  const d = await bankApi.getPaper(p.id)
  basket.addMany(d.questions.map(fromBank))
}
</script>

<template>
  <main class="cp container">
    <nav class="crumb" aria-label="当前位置">
      <RouterLink to="/">首页</RouterLink><span>›</span><span>章节选题</span>
      <template v-if="book"><span>›</span><span>{{ version?.name }} {{ book.name }}</span></template>
    </nav>

    <div class="layout">
      <aside class="card side sticky-side">
        <div ref="picker" class="book">
          <button v-if="book" class="book-btn" :aria-expanded="pickerOpen" @click="pickerOpen = !pickerOpen">
            <span class="icon" aria-hidden="true">≡</span>
            <span class="ver">{{ version?.name }}</span><span class="gt">›</span><span class="bk">{{ book.name }}</span>
            <span class="caret" aria-hidden="true">{{ pickerOpen ? '▴' : '▾' }}</span>
          </button>
          <div v-if="pickerOpen" class="book-pop">
            <div class="pop-row">
              <span class="pop-label">版本</span>
              <div class="pop-opts">
                <button v-for="v in versions" :key="v.name" class="pop-opt" :class="{ on: v.name === version?.name }" @click="pickVersion(v)">
                  {{ v.name }}<span v-if="v.region" class="region">{{ v.region }}</span>
                </button>
              </div>
            </div>
            <div class="pop-row">
              <span class="pop-label">教材</span>
              <div class="pop-opts">
                <button
                  v-for="b in version?.books" :key="b.id" class="pop-opt" :class="{ on: b.id === book?.id }"
                  :title="b.edition === '旧教材' ? '平台尚未上线新教材目录，暂用旧教材目录' : undefined" @click="pickBook(b)"
                >{{ b.name }}<span v-if="b.edition === '旧教材'" class="old">旧版目录</span></button>
              </div>
            </div>
            <p class="pop-note">
              {{ version?.region ? `${version.region}市现用版本；` : '' }}目录来自国家中小学智慧教育平台，各节按知识点归入题目。
            </p>
          </div>
        </div>

        <div class="tree">
          <p v-if="loadError" class="tip err">{{ loadError }}</p>
          <p v-else-if="!loaded" class="tip">加载中…</p>
          <p v-else-if="!book" class="tip">
            暂无{{ stage }}{{ subject }}的教材章节目录，可先用<RouterLink to="/knowledge">知识点选题</RouterLink>。
          </p>
          <template v-else>
            <div class="tree-tools">
              <button class="all" :class="{ on: !chapterId }" @click="chapterId = null">全部章节</button>
              <label class="only"><input v-model="onlyWithQuestions" type="checkbox"> 只看有题</label>
            </div>
            <TreeNav
              :nodes="tree" :counts="counts" :selected="chapterId ? [chapterId] : []" :open-depth="1"
              :only-with-questions="onlyWithQuestions" @select="chapterId = $event.id"
            />
          </template>
        </div>
      </aside>

      <section class="main">
        <div class="card tabs">
          <button class="tab" :class="{ on: tab === 'questions' }" @click="tab = 'questions'">试题</button>
          <button class="tab" :class="{ on: tab === 'papers' }" @click="tab = 'papers'">同步套卷</button>
          <span v-if="tab === 'questions'" class="cur">当前：<b>{{ currentName }}</b></span>
        </div>

        <QuestionBrowser
          v-if="tab === 'questions'" :stage="stage" :subject="subject"
          :scope="{ chapterId: chapterId ?? undefined }"
        />

        <div v-else class="card papers">
          <p v-if="papersError" class="tip err">{{ papersError }}</p>
          <p v-else-if="!papers" class="tip">加载中…</p>
          <p v-else-if="!papers.length" class="tip">试卷库中还没有{{ stage }}{{ subject }}的试卷。</p>
          <div v-for="p in papers ?? []" :key="p.id" class="paper-row">
            <span class="tag">{{ p.meta.paperType || '试卷' }}</span>
            <div class="paper-main">
              <RouterLink :to="`/papers/${p.id}`" class="paper-title">{{ p.title }}</RouterLink>
              <span class="paper-meta">{{ [p.meta.grade, p.meta.region, p.meta.schoolYear].filter(Boolean).join(' · ') }} · {{ p.questionCount }} 题 · 总分 {{ p.totalScore }}</span>
            </div>
            <button class="paper-add" @click="addPaper(p)">整卷加入试题篮</button>
          </div>
          <RouterLink v-if="papers?.length" :to="{ path: '/papers', query: { stage, subject } }" class="more">在试卷选题中查看全部 ›</RouterLink>
        </div>
      </section>
    </div>

    <BasketFloat />
  </main>
</template>

<style scoped>
.cp { padding-top: 14px; padding-bottom: 56px; }
.crumb { display: flex; gap: 8px; font-size: 13px; color: var(--c-text-4); margin-bottom: 12px; }
.crumb a { color: var(--c-text-3); }
.layout { display: flex; gap: 18px; align-items: flex-start; }

.side { flex: 0 0 300px; min-width: 0; }
.book { position: relative; border-bottom: 1px solid var(--c-divider); }
.book-btn {
  width: 100%; display: flex; align-items: center; gap: 6px; border: none; background: none; padding: 16px 18px;
  font-size: 16px; color: var(--c-primary); text-align: left;
}
.icon { color: var(--c-primary); }
.gt { color: var(--c-text-4); }
.caret { margin-left: auto; font-size: 11px; }
.book-pop {
  position: absolute; left: 0; top: 100%; z-index: 12; width: min(760px, calc(100vw - 32px)); background: #fff;
  border: 1px solid var(--c-border); border-radius: var(--r-lg); box-shadow: 0 16px 40px rgba(27, 36, 48, .14);
  padding: 14px 18px; display: flex; flex-direction: column; gap: 10px;
}
.pop-row { display: flex; gap: 14px; align-items: flex-start; }
.pop-label { width: 40px; flex-shrink: 0; font-size: 14px; color: var(--c-text-4); line-height: 34px; }
.pop-opts { display: flex; flex-wrap: wrap; gap: 6px; }
.pop-opt { border: none; background: none; padding: 6px 14px; font-size: 14px; color: var(--c-text-2); border-radius: var(--r-sm); }
.pop-opt:hover { color: var(--c-primary); }
.pop-opt.on { background: var(--c-primary-soft); color: var(--c-primary); font-weight: 600; }
.region, .old { margin-left: 4px; font-size: 11px; font-weight: 400; padding: 0 5px; border-radius: 3px; }
.region { color: #fff; background: var(--c-primary); }
.old { color: var(--c-text-4); background: var(--c-paper); }
.pop-note { margin: 0; padding-top: 8px; border-top: 1px dashed var(--c-divider); font-size: 12px; color: var(--c-text-4); }

.tree { padding: 10px 10px 16px; max-height: calc(100vh - 200px); overflow: auto; }
.tree-tools { display: flex; align-items: center; justify-content: space-between; padding: 0 4px 6px; }
.all { border: none; background: none; padding: 5px 8px; font-size: 14px; color: var(--c-text-2); border-radius: var(--r-sm); display: flex; gap: 6px; align-items: center; }
.all.on { color: var(--c-primary); font-weight: 600; background: var(--c-primary-soft); }
.only { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--c-text-3); cursor: pointer; }
.tip { margin: 0; padding: 10px 8px; font-size: 13px; color: var(--c-text-4); line-height: 1.7; }
.tip.err { color: var(--c-hard); }

.main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 14px; }
.tabs { display: flex; align-items: center; gap: 28px; padding: 0 24px; }
.tab { border: none; background: none; padding: 14px 0 12px; font-size: 16px; color: var(--c-text-2); border-bottom: 2px solid transparent; }
.tab.on { color: var(--c-primary); font-weight: 600; border-bottom-color: var(--c-primary); }
.cur { margin-left: auto; font-size: 13px; color: var(--c-text-3); }
.cur b { color: var(--c-ink); }

.papers { padding: 6px 22px 14px; }
.paper-row { display: flex; align-items: center; gap: 12px; padding: 14px 0; border-bottom: 1px dashed var(--c-divider); }
.paper-row .tag { flex-shrink: 0; padding: 2px 6px; }
.paper-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.paper-title { color: var(--c-ink); font-size: 15px; }
.paper-title:hover { color: var(--c-primary); }
.paper-meta { font-size: 12px; color: var(--c-text-4); }
.paper-add { flex-shrink: 0; border: 1px solid var(--c-primary); background: #fff; color: var(--c-primary); border-radius: var(--r-sm); padding: 5px 12px; font-size: 13px; }
.paper-add:hover { background: var(--c-primary-soft); }
.more { display: inline-block; margin-top: 12px; font-size: 13px; }

@media (max-width: 900px) {
  .layout { flex-direction: column; align-items: stretch; }
  .side { flex: none; }
  .sticky-side { position: static; }
  .tree { max-height: 320px; }
}
</style>
