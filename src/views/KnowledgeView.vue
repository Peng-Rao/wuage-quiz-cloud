<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { parseApi, type KnowledgeTree, type KnowledgeTreeDetail, type KnowledgeTreeNode } from '@/api/parse'
import { STAGES } from '@/data/mock'
import KnowledgeNodeItem from '@/components/parse/KnowledgeNodeItem.vue'

const trees = ref<KnowledgeTree[]>([])
const current = ref<KnowledgeTreeDetail | null>(null)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const keyword = ref('')

async function load() {
  trees.value = await parseApi.listTrees().catch((e) => ((error.value = e.message), []))
  if (!current.value && trees.value.length) open(trees.value[0].id)
}
onMounted(load)

async function open(id: string) {
  error.value = ''
  current.value = await parseApi.getTree(id).catch((e) => ((error.value = e.message), null))
}

async function remove(t: KnowledgeTree) {
  if (!confirm(t.builtin ? `删除内置知识树「${t.name}」？服务重启后会重新载入。` : `删除「${t.name}」？已标注的题目保留原知识点名称。`)) return
  await parseApi.deleteTree(t.id).catch((e) => (error.value = e.message))
  if (current.value?.id === t.id) current.value = null
  await load()
}

// ---- 导入 ----
const form = reactive({ format: 'csv' as 'json' | 'csv', content: '', fileName: '', name: '', stage: '高中', subject: '数学', textbook: '' })
const subjects = computed(() => STAGES[form.stage] ?? [])
watch(() => form.stage, () => {
  if (!subjects.value.includes(form.subject)) form.subject = subjects.value[0] ?? ''
})

async function onFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  form.content = await f.text()
  form.fileName = f.name
  form.format = /\.json$/i.test(f.name) ? 'json' : 'csv'
  if (!form.name) form.name = f.name.replace(/\.[^.]+$/, '')
}

async function submit() {
  error.value = ''
  notice.value = ''
  busy.value = true
  try {
    const t = await parseApi.importTree({
      format: form.format, content: form.content, name: form.name || undefined,
      subject: form.subject, stage: form.stage, textbook: form.textbook || undefined,
    })
    notice.value = `已导入「${t.name}」，共 ${t.nodeCount} 个知识点；解析${t.stage}${t.subject}试卷时将优先使用`
    Object.assign(form, { content: '', fileName: '', name: '' })
    await load()
    await open(t.id)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

const CSV_EXAMPLE = `一级,二级,三级,别名
第一章 集合,1.3 集合的基本运算,交集,交运算|A∩B
第一章 集合,1.3 集合的基本运算,并集,
第二章 函数,函数的单调性,,单调性`

// ---- 浏览 ----
function matches(n: KnowledgeTreeNode, kw: string): boolean {
  return n.name.includes(kw) || n.aliases.some((a) => a.includes(kw)) || n.children.some((c) => matches(c, kw))
}
const shownNodes = computed(() => {
  const kw = keyword.value.trim()
  return current.value ? (kw ? current.value.nodes.filter((n) => matches(n, kw)) : current.value.nodes) : []
})
</script>

<template>
  <main class="kt container">
    <div class="top">
      <div>
        <RouterLink to="/upload" class="back">‹ 试卷解析</RouterLink>
        <h1>知识树管理</h1>
        <span class="sub">解析试卷时按学科、学段选用知识树标注知识点：学校导入的优先于内置知识树，教材版本一致的优先。内置知识树由《基础学科知识点总纲》整理，覆盖小学至高中 23 个学科。</span>
      </div>
    </div>
    <p v-if="error" class="card err" role="alert">{{ error }}</p>
    <p v-if="notice" class="card ok">{{ notice }}</p>

    <div class="row">
      <aside class="side">
        <div class="card panel">
          <span class="card-title">知识树</span>
          <ul class="list">
            <li v-for="t in trees" :key="t.id" :class="{ on: current?.id === t.id }">
              <button class="main" @click="open(t.id)">
                <span class="name">{{ t.name }}</span>
                <span class="meta">{{ t.stage }} · {{ t.subject }}<template v-if="t.textbook"> · {{ t.textbook }}</template> · {{ t.nodeCount }} 个</span>
              </button>
              <span v-if="t.builtin" class="tag">内置</span>
              <button class="btn-link small del" @click="remove(t)">删除</button>
            </li>
          </ul>
          <p v-if="!trees.length" class="muted small">还没有知识树</p>
        </div>

        <form class="card panel" @submit.prevent="submit">
          <span class="card-title">导入知识树</span>
          <label class="file">
            <input type="file" accept=".json,.csv,.txt" @change="onFile">
            <span>{{ form.fileName || '选择 JSON / CSV 文件' }}</span>
          </label>
          <div class="grid">
            <label>学段
              <select v-model="form.stage"><option v-for="s in Object.keys(STAGES)" :key="s">{{ s }}</option></select>
            </label>
            <label>学科
              <select v-model="form.subject"><option v-for="s in subjects" :key="s">{{ s }}</option></select>
            </label>
            <label class="wide">名称 <input v-model="form.name" placeholder="如：高中数学知识体系（校本）"></label>
            <label class="wide">教材版本 <input v-model="form.textbook" placeholder="如：人教A版（2019），可留空"></label>
          </div>
          <details class="help">
            <summary>文件格式</summary>
            <p><b>CSV</b>：每行一条路径，列为各级名称，最后一列可写别名（多个用 | 分隔）：</p>
            <pre>{{ CSV_EXAMPLE }}</pre>
            <p><b>JSON</b>：<code>{"nodes": [{"name": "…", "aliases": [], "children": […]}]}</code>，可带 name / subject / stage / textbook。</p>
          </details>
          <button class="btn btn-primary" :disabled="busy || !form.content">{{ busy ? '导入中…' : '导入' }}</button>
        </form>
      </aside>

      <section class="card panel tree">
        <template v-if="current">
          <div class="tree-head">
            <div>
              <span class="card-title">{{ current.name }}</span>
              <span class="muted small">{{ current.stage }} · {{ current.subject }} · {{ current.nodeCount }} 个知识点<template v-if="current.builtin"> · 内置（由知识点总纲整理）</template></span>
            </div>
            <input v-model="keyword" class="search" placeholder="搜索知识点或别名">
          </div>
          <ul class="nodes">
            <KnowledgeNodeItem v-for="n in shownNodes" :key="n.id" :node="n" :keyword="keyword.trim()" :depth="0" />
          </ul>
          <p v-if="!shownNodes.length" class="muted small">没有匹配的知识点</p>
        </template>
        <p v-else class="muted">选择左侧的知识树查看</p>
      </section>
    </div>
  </main>
</template>

<style scoped>
.kt { width: 100%; padding-top: 24px; padding-bottom: 56px; display: flex; flex-direction: column; gap: 16px; }
.back { font-size: 13px; color: var(--c-text-3); }
h1 { margin: 4px 0 6px; font-size: 24px; }
.sub { font-size: 14px; color: var(--c-text-3); }
.err, .ok { margin: 0; padding: 10px 16px; font-size: 13px; }
.err { color: var(--c-danger); background: var(--c-danger-soft); border-color: var(--c-danger-line); }
.ok { color: var(--c-success); background: var(--c-success-soft); border-color: var(--c-success-line); }
.row { display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; }
.side { flex: 1 0 300px; max-width: 360px; display: flex; flex-direction: column; gap: 14px; }
.panel { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
.small { font-size: 12px; margin: 0; }
.list { list-style: none; margin: 0; padding: 0; }
.list li { display: flex; align-items: center; gap: 8px; border-top: 1px solid var(--c-divider); }
.list li:first-child { border-top: none; }
.list li.on .name { color: var(--c-primary); font-weight: 600; }
.main { flex: 1; min-width: 0; text-align: left; border: none; background: none; padding: 8px 0; display: flex; flex-direction: column; gap: 2px; color: var(--c-ink); }
.name { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { font-size: 12px; color: var(--c-text-4); }
.tag { font-size: 11px; color: var(--c-primary-dark); background: var(--c-primary-soft); border-radius: 4px; padding: 0 6px; flex-shrink: 0; }
.del { flex-shrink: 0; }
.del:hover { color: var(--c-danger); }
.file { border: 1px dashed var(--c-primary-line); border-radius: var(--r-md); padding: 12px; font-size: 13px; color: var(--c-text-2); cursor: pointer; text-align: center; }
.file input { display: none; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.grid label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--c-text-3); }
.grid .wide { grid-column: 1 / -1; }
.grid input, .grid select, .search { border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: 6px 8px; font-size: 13px; font-family: inherit; color: var(--c-ink); background: var(--c-surface); }
.help { font-size: 12px; color: var(--c-text-3); }
.help summary { cursor: pointer; }
.help pre { background: var(--c-paper); border-radius: var(--r-sm); padding: 8px; overflow-x: auto; font-size: 11.5px; }
.help p { margin: 6px 0; }
.tree { flex: 999 1 480px; min-width: 0; }
.tree-head { display: flex; flex-wrap: wrap; gap: 10px; justify-content: space-between; align-items: center; }
.tree-head > div { display: flex; flex-direction: column; gap: 2px; }
.search { width: 220px; }
.nodes { list-style: none; margin: 0; padding: 0; max-height: 70vh; overflow-y: auto; }
</style>
