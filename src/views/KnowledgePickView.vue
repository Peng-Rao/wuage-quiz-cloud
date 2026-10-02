<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { bankApi } from '@/api/bank'
import { parseApi, type KnowledgeTree, type KnowledgeTreeDetail } from '@/api/parse'
import { useAppStore } from '@/stores/app'
import QuestionBrowser from '@/components/bank/QuestionBrowser.vue'
import TreeNav, { type TreeItem } from '@/components/bank/TreeNav.vue'
import BasketFloat from '@/components/bank/BasketFloat.vue'
import LoadingState from '@/components/LoadingState.vue'

const { stage, subject } = storeToRefs(useAppStore())

const trees = ref<KnowledgeTree[]>([])
const treeId = ref<string | null>(null)
const tree = ref<KnowledgeTreeDetail | null>(null)
const counts = ref<Record<string, number>>({})
const error = ref('')
const loaded = ref(false)
let treesSeq = 0

/** 当前学段学科的知识树：正式导入的优先于内置，其次最新导入的（与后端 pick_tree 一致） */
async function loadTrees() {
  const seq = ++treesSeq
  loaded.value = false
  error.value = ''
  treeId.value = null
  tree.value = null
  try {
    const next = await parseApi.listTrees()
    if (seq !== treesSeq) return
    trees.value = next
      .filter((t) => t.stage === stage.value && t.subject === subject.value)
      .sort((a, b) => Number(a.builtin) - Number(b.builtin) || b.createdAt.localeCompare(a.createdAt))
  } catch (e) {
    if (seq !== treesSeq) return
    trees.value = []
    error.value = (e as Error).message
  }
  const next = trees.value[0]?.id ?? null
  treeId.value = next
  if (!next) loaded.value = true
}
watch([stage, subject], loadTrees, { immediate: true })

watch(treeId, async (id, _, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  selected.value = []
  tree.value = null
  counts.value = {}
  if (!id) return
  loaded.value = false
  error.value = ''
  try {
    const [t, c] = await Promise.all([parseApi.getTree(id), bankApi.knowledgeCounts(id)])
    if (!active) return
    tree.value = t
    counts.value = c
  } catch (e) {
    if (active) error.value = (e as Error).message
  } finally {
    if (active) loaded.value = true
  }
})

// ---------- 选择 ----------

const multi = ref(false)
const keyword = ref('')
const onlyWithQuestions = ref(false)
const selected = ref<string[]>([])
const names = computed(() => {
  const m = new Map<string, string>()
  const walk = (ns: TreeItem[]) => ns.forEach((n) => { m.set(n.id, n.name); walk(n.children ?? []) })
  walk(tree.value?.nodes ?? [])
  return m
})

function select(n: TreeItem) {
  if (!multi.value) {
    selected.value = selected.value[0] === n.id ? [] : [n.id]
    return
  }
  selected.value = selected.value.includes(n.id) ? selected.value.filter((x) => x !== n.id) : [...selected.value, n.id]
}
watch(multi, (m) => { if (!m && selected.value.length > 1) selected.value = selected.value.slice(-1) })

const current = computed(() => {
  if (!selected.value.length) return '全部知识点'
  const list = selected.value.map((id) => names.value.get(id) ?? '')
  return list.length > 3 ? `${list.slice(0, 3).join('、')} 等 ${list.length} 个知识点` : list.join('、')
})
</script>

<template>
  <main class="kp container">
    <nav class="crumb" aria-label="当前位置">
      <RouterLink to="/">首页</RouterLink><span>›</span><span>知识点选题</span>
      <template v-if="tree"><span>›</span><span>{{ tree.name }}</span></template>
    </nav>

    <div class="layout">
      <aside class="card side sticky-side">
        <div class="side-tabs"><span class="side-tab on">知识点</span></div>
        <div class="tools">
          <input v-model="keyword" class="search" placeholder="知识点立即查询" aria-label="查询知识点">
          <button class="multi" :class="{ on: multi }" :aria-pressed="multi" @click="multi = !multi">
            <span class="dot" aria-hidden="true" />多选
          </button>
        </div>
        <div class="tools sub">
          <select v-if="trees.length > 1" v-model="treeId" class="tree-select" aria-label="知识体系">
            <option v-for="t in trees" :key="t.id" :value="t.id">{{ t.name }}{{ t.builtin ? '（内置）' : '' }}</option>
          </select>
          <span v-else-if="tree" class="tree-name">{{ tree.name }}</span>
          <label class="only"><input v-model="onlyWithQuestions" type="checkbox"> 只看有题</label>
        </div>

        <div class="tree">
          <p v-if="error" class="tip err">{{ error }}</p>
          <LoadingState v-else-if="!loaded" label="正在加载知识体系…" detail="正在获取知识点与题量统计" />
          <p v-else-if="!trees.length" class="tip">
            {{ stage }}{{ subject }}还没有知识树，可在 <RouterLink to="/upload/knowledge">知识树管理</RouterLink> 中导入。
          </p>
          <template v-else-if="tree">
            <button class="all" :class="{ on: !selected.length }" @click="selected = []">全部知识点</button>
            <TreeNav
              :nodes="tree.nodes" :counts="counts" :selected="selected" :multi="multi" :keyword="keyword"
              :only-with-questions="onlyWithQuestions" :open-depth="1" @select="select"
            />
          </template>
        </div>
      </aside>

      <section class="main">
        <div class="card cur-bar">
          <span>当前：<b>{{ current }}</b></span>
          <button v-if="selected.length" class="btn-link" @click="selected = []">清除</button>
          <span v-if="multi" class="hint">多选时，含任一所选知识点的题都会列出</span>
        </div>
        <QuestionBrowser
          :stage="stage" :subject="subject"
          :scope="{ treeId: treeId ?? undefined, nodeIds: selected }"
        />
      </section>
    </div>

    <BasketFloat />
  </main>
</template>

<style scoped>
.kp { padding-top: 14px; padding-bottom: 56px; }
.crumb { display: flex; gap: 8px; font-size: 13px; color: var(--c-text-4); margin-bottom: 12px; }
.crumb a { color: var(--c-text-3); }
.layout { display: flex; gap: 18px; align-items: flex-start; }

.side { flex: 0 0 300px; min-width: 0; }
.side-tabs { display: flex; justify-content: center; border-bottom: 1px solid var(--c-divider); margin: 0 18px; }
.side-tab { padding: 14px 0 10px; font-size: 16px; color: var(--c-text-2); border-bottom: 2px solid transparent; }
.side-tab.on { color: var(--c-primary); font-weight: 600; border-bottom-color: var(--c-primary); }
.tools { display: flex; gap: 8px; align-items: center; padding: 12px 18px 0; }
.tools.sub { padding-top: 8px; justify-content: space-between; }
.search {
  flex: 1; min-width: 0; height: 32px; border: 1px solid var(--c-border); border-radius: 16px; padding: 0 12px 0 30px; font-size: 13px;
  background: #fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' fill='none' stroke='%238A8F95' stroke-width='2'%3E%3Ccircle cx='6' cy='6' r='4.5'/%3E%3Cpath d='m9.5 9.5 3 3'/%3E%3C/svg%3E") no-repeat 11px center;
}
.search:focus { outline: none; border-color: var(--c-primary); }
.multi {
  flex-shrink: 0; height: 32px; border: none; border-radius: 16px; padding: 0 12px; background: var(--c-paper);
  color: var(--c-text-3); font-size: 13px; display: flex; align-items: center; gap: 6px;
}
.multi .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--c-toggle-off); transition: background .15s; }
.multi.on { background: var(--c-primary-soft); color: var(--c-primary); }
.multi.on .dot { background: var(--c-primary); }
.tree-select {
  min-width: 0; flex: 1; height: 28px; border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: 0 6px;
  font-size: 12px; color: var(--c-text-2); background: #fff; font-family: inherit;
}
.tree-name { font-size: 12px; color: var(--c-text-4); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.only { flex-shrink: 0; display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--c-text-3); cursor: pointer; }
.tree { padding: 8px 10px 16px; max-height: calc(100vh - 250px); overflow: auto; }
.all { border: none; background: none; padding: 5px 8px 5px 30px; font-size: 14px; color: var(--c-text-2); border-radius: var(--r-sm); }
.all.on { color: var(--c-primary); font-weight: 600; background: var(--c-primary-soft); }
.tip { margin: 0; padding: 10px 8px; font-size: 13px; color: var(--c-text-4); line-height: 1.7; }
.tip.err { color: var(--c-hard); }

.main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 14px; }
.cur-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; padding: 12px 22px; font-size: 14px; color: var(--c-text-3); }
.cur-bar b { color: var(--c-ink); }
.hint { margin-left: auto; font-size: 12px; color: var(--c-text-4); }

@media (max-width: 900px) {
  .layout { flex-direction: column; align-items: stretch; }
  .side { flex: none; }
  .sticky-side { position: static; }
  .tree { max-height: 320px; }
}
</style>
