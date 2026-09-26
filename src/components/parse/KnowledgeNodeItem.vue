<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { KnowledgeTreeNode } from '@/api/parse'

const props = defineProps<{ node: KnowledgeTreeNode; keyword: string; depth: number }>()

const hit = (n: KnowledgeTreeNode, kw: string): boolean =>
  n.name.includes(kw) || n.aliases.some((a) => a.includes(kw)) || n.children.some((c) => hit(c, kw))

// 默认展开前两级；搜索时展开所有命中的分支
const open = ref(props.depth < 1)
watch(() => props.keyword, (kw) => { open.value = kw ? hit(props.node, kw) : props.depth < 1 }, { immediate: true })
const children = computed(() => (props.keyword ? props.node.children.filter((c) => hit(c, props.keyword)) : props.node.children))
const self = computed(() => !!props.keyword && (props.node.name.includes(props.keyword) || props.node.aliases.some((a) => a.includes(props.keyword))))
</script>

<template>
  <li>
    <div class="row" :style="{ paddingLeft: depth * 18 + 'px' }">
      <button v-if="node.children.length" class="caret" :aria-expanded="open" @click="open = !open">{{ open ? '▾' : '▸' }}</button>
      <span v-else class="caret leaf">•</span>
      <span class="name" :class="{ hit: self, leaf: !node.children.length }">{{ node.name }}</span>
      <span v-if="node.aliases.length" class="alias">别名：{{ node.aliases.join('、') }}</span>
      <span v-if="node.children.length" class="count">{{ node.children.length }}</span>
    </div>
    <ul v-if="open && children.length">
      <KnowledgeNodeItem v-for="c in children" :key="c.id" :node="c" :keyword="keyword" :depth="depth + 1" />
    </ul>
  </li>
</template>

<style scoped>
ul { list-style: none; margin: 0; padding: 0; }
.row { display: flex; align-items: baseline; gap: 6px; padding-top: 4px; padding-bottom: 4px; font-size: 13.5px; }
.caret { width: 16px; flex-shrink: 0; border: none; background: none; padding: 0; color: var(--c-text-3); font-size: 12px; text-align: center; }
.caret.leaf { color: var(--c-text-4); }
.name.leaf { color: var(--c-text-2); }
.name.hit { background: var(--c-primary-soft); color: var(--c-primary-dark); border-radius: 3px; padding: 0 3px; }
.alias { font-size: 11.5px; color: var(--c-text-4); }
.count { font-size: 11px; color: var(--c-text-4); }
</style>
