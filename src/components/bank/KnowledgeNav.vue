<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { KnowledgeTreeNode } from '@/api/parse'

/** 知识点目录：逐级展开，显示各节点（含下级）的入库题数 */
const props = defineProps<{
  nodes: KnowledgeTreeNode[]
  counts: Record<string, number>
  selected: string | null
  /** 只显示有题的知识点 */
  onlyWithQuestions?: boolean
}>()
const emit = defineEmits<{ select: [node: KnowledgeTreeNode | null] }>()

const open = ref(new Set<string>())
// 换树后默认展开第一级
watch(() => props.nodes, (nodes) => { open.value = new Set(nodes.slice(0, 1).map((n) => n.id)) }, { immediate: true })

function toggle(id: string) {
  const s = new Set(open.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  open.value = s
}

const rows = computed(() => {
  const out: { node: KnowledgeTreeNode; depth: number; count: number }[] = []
  const walk = (nodes: KnowledgeTreeNode[], depth: number) => {
    for (const n of nodes) {
      const count = props.counts[n.id] ?? 0
      if (props.onlyWithQuestions && !count) continue
      out.push({ node: n, depth, count })
      if (n.children.length && open.value.has(n.id)) walk(n.children, depth + 1)
    }
  }
  walk(props.nodes, 0)
  return out
})
</script>

<template>
  <div class="kn">
    <button class="kn-row all" :class="{ on: !selected }" @click="emit('select', null)">全部知识点</button>
    <div v-for="r in rows" :key="r.node.id" class="kn-line" :style="{ paddingLeft: r.depth * 14 + 'px' }">
      <button
        v-if="r.node.children.length" class="caret" :aria-expanded="open.has(r.node.id)"
        :aria-label="open.has(r.node.id) ? '收起' : '展开'" @click="toggle(r.node.id)"
      >{{ open.has(r.node.id) ? '▼' : '▶' }}</button>
      <span v-else class="caret leaf" />
      <button
        class="kn-row" :class="{ on: selected === r.node.id, empty: !r.count, top: r.depth === 0 }"
        :title="r.node.name" @click="emit('select', r.node)"
      >
        <span class="name">{{ r.node.name }}</span>
        <span v-if="r.count" class="n">{{ r.count }}</span>
      </button>
    </div>
    <div v-if="!rows.length" class="kn-empty">{{ onlyWithQuestions ? '题库中还没有标注到本知识体系的题目。' : '知识树为空。' }}</div>
  </div>
</template>

<style scoped>
.kn { display: flex; flex-direction: column; gap: 1px; }
.kn-line { display: flex; align-items: flex-start; gap: 2px; }
.caret {
  width: 18px; height: 30px; flex-shrink: 0; border: none; background: none; padding: 0;
  font-size: 9px; color: var(--c-text-4);
}
.caret.leaf { display: inline-block; }
.kn-row {
  flex: 1; min-width: 0; border: none; background: transparent; text-align: left; padding: 6px 8px;
  font-size: 13px; line-height: 1.5; color: var(--c-text-2); border-radius: var(--r-sm);
  display: flex; align-items: flex-start; gap: 6px;
}
.kn-row.top { color: var(--c-ink); font-weight: 500; }
.kn-row.all { flex: none; margin-left: 20px; }
.kn-row:hover { background: var(--c-paper); }
.kn-row.on { background: var(--c-primary-soft); color: var(--c-primary); font-weight: 600; }
.kn-row.empty:not(.on) { color: var(--c-text-4); }
.name { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.n { flex-shrink: 0; font-size: 11px; color: var(--c-text-4); background: var(--c-paper); border-radius: 8px; padding: 0 6px; line-height: 18px; margin-top: 1px; }
.kn-row.on .n { background: #fff; color: var(--c-primary); }
.kn-empty { padding: 12px 20px; font-size: 13px; color: var(--c-text-4); line-height: 1.6; }
</style>
