<script setup lang="ts">
import { computed, ref, watch } from 'vue'

export interface TreeItem {
  id: string
  name: string
  children?: TreeItem[]
}

/** 章节 / 知识点目录：＋／－ 逐级展开，显示各节点（含下级）的题数，支持单选、多选与关键词过滤 */
const props = withDefaults(defineProps<{
  nodes: TreeItem[]
  counts: Record<string, number>
  selected: string[]
  multi?: boolean
  /** 只显示名称含该关键词的节点（及其上级），并全部展开 */
  keyword?: string
  /** 只显示有题的节点 */
  onlyWithQuestions?: boolean
  /** 默认展开的层数 */
  openDepth?: number
}>(), { multi: false, keyword: '', onlyWithQuestions: false, openDepth: 1 })
const emit = defineEmits<{ select: [item: TreeItem] }>()

const open = ref(new Set<string>())
watch(() => props.nodes, (nodes) => {
  const s = new Set<string>()
  const walk = (ns: TreeItem[], depth: number) => ns.forEach((n) => {
    if (depth < props.openDepth && n.children?.length) {
      s.add(n.id)
      walk(n.children, depth + 1)
    }
  })
  walk(nodes, 0)
  open.value = s
}, { immediate: true })

function toggle(id: string) {
  const s = new Set(open.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  open.value = s
}

const kw = computed(() => props.keyword.trim())
/** 关键词命中的节点及其上级 */
const visible = computed(() => {
  if (!kw.value) return null
  const keep = new Set<string>()
  const walk = (ns: TreeItem[]): boolean => ns.reduce((any, n) => {
    const hit = n.name.includes(kw.value) || walk(n.children ?? [])
    if (hit) keep.add(n.id)
    return any || hit
  }, false)
  walk(props.nodes)
  return keep
})

const rows = computed(() => {
  const out: { item: TreeItem; depth: number; count: number; hasChildren: boolean; isOpen: boolean }[] = []
  const walk = (ns: TreeItem[], depth: number) => {
    for (const n of ns) {
      const count = props.counts[n.id] ?? 0
      if (visible.value && !visible.value.has(n.id)) continue
      if (props.onlyWithQuestions && !count) continue
      const hasChildren = !!n.children?.length
      const isOpen = hasChildren && (!!visible.value || open.value.has(n.id))
      out.push({ item: n, depth, count, hasChildren, isOpen })
      if (isOpen) walk(n.children!, depth + 1)
    }
  }
  walk(props.nodes, 0)
  return out
})

const sel = computed(() => new Set(props.selected))
</script>

<template>
  <div class="tn" role="tree">
    <div
      v-for="r in rows" :key="r.item.id" class="tn-row" :class="{ top: r.depth === 0 }"
      :style="{ paddingLeft: r.depth * 16 + 'px' }" role="treeitem" :aria-expanded="r.hasChildren ? r.isOpen : undefined"
      :aria-selected="sel.has(r.item.id)"
    >
      <button
        v-if="r.hasChildren" class="tn-toggle" :aria-label="r.isOpen ? '收起' : '展开'" @click="toggle(r.item.id)"
      >{{ r.isOpen ? '−' : '+' }}</button>
      <span v-else class="tn-leaf" aria-hidden="true" />
      <button class="tn-name" :class="{ on: sel.has(r.item.id), empty: !r.count }" :title="r.item.name" @click="emit('select', r.item)">
        <span v-if="multi" class="check" :class="{ on: sel.has(r.item.id) }" aria-hidden="true">{{ sel.has(r.item.id) ? '✓' : '' }}</span>
        <span class="label">{{ r.item.name }}</span>
        <span v-if="r.count" class="n">{{ r.count }}</span>
      </button>
    </div>
    <p v-if="!rows.length" class="tn-empty">
      {{ kw ? `没有名称含「${kw}」的节点` : onlyWithQuestions ? '还没有归入这些目录的题目。' : '目录为空。' }}
    </p>
  </div>
</template>

<style scoped>
.tn { display: flex; flex-direction: column; gap: 1px; }
.tn-row { display: flex; align-items: flex-start; gap: 4px; }
.tn-toggle {
  flex-shrink: 0; width: 16px; height: 16px; margin: 8px 4px 0 2px; padding: 0; border-radius: 50%;
  border: 1px solid var(--c-border); background: var(--c-surface); color: var(--c-text-3); font-size: 12px; line-height: 13px;
}
.tn-row.top > .tn-toggle { border-color: var(--c-primary-line); color: var(--c-primary); }
.tn-toggle:hover { border-color: var(--c-primary); color: var(--c-primary); }
.tn-leaf { flex-shrink: 0; width: 16px; margin: 0 4px 0 2px; }
.tn-name {
  flex: 1; min-width: 0; border: none; background: transparent; text-align: left; padding: 5px 8px;
  font-size: 14px; line-height: 1.6; color: var(--c-text-2); border-radius: var(--r-sm);
  display: flex; align-items: flex-start; gap: 6px;
}
.tn-row.top .tn-name { color: var(--c-ink); font-weight: 500; }
.tn-name:hover { background: var(--c-paper); color: var(--c-primary); }
.tn-name.on { color: var(--c-primary); font-weight: 600; background: var(--c-primary-soft); }
.tn-name.empty:not(.on) { color: var(--c-text-4); }
.label { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.check {
  flex-shrink: 0; width: 14px; height: 14px; margin-top: 4px; border: 1px solid var(--c-border); border-radius: 3px;
  font-size: 10px; line-height: 12px; text-align: center; color: #fff; background: var(--c-surface);
}
.check.on { background: var(--c-primary); border-color: var(--c-primary); }
.n { flex-shrink: 0; font-size: 11px; color: var(--c-text-4); background: var(--c-paper); border-radius: 8px; padding: 0 6px; line-height: 18px; margin-top: 3px; }
.tn-name.on .n { background: var(--c-surface); color: var(--c-primary); }
.tn-empty { margin: 0; padding: 12px 8px; font-size: 13px; color: var(--c-text-4); line-height: 1.6; }
</style>
