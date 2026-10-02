<script setup lang="ts">
import { computed } from 'vue'
import { formatPercent } from '@/utils/usage'

export interface BreakdownItem {
  key: string
  label: string
  /** 名称下方的补充说明 */
  sub?: string
  value: number
}

const props = defineProps<{ items: BreakdownItem[]; format: (v: number) => string; empty?: string }>()

const total = computed(() => props.items.reduce((a, b) => a + b.value, 0))
const max = computed(() => Math.max(...props.items.map(i => i.value), 0))
</script>

<template>
  <p v-if="!items.length" class="muted empty">{{ empty ?? '暂无数据' }}</p>
  <ul v-else class="list">
    <li v-for="it in items" :key="it.key">
      <div class="name">
        <span>{{ it.label }}</span>
        <small v-if="it.sub">{{ it.sub }}</small>
      </div>
      <div class="track" aria-hidden="true">
        <span :style="{ width: max ? (it.value / max) * 100 + '%' : '0' }" />
      </div>
      <div class="num">
        <b>{{ format(it.value) }}</b>
        <small>{{ total ? formatPercent(it.value / total) : '—' }}</small>
      </div>
    </li>
  </ul>
</template>

<style scoped>
.empty { font-size: 13px; margin: 0; }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
li { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(60px, 1fr) auto; align-items: center; gap: 12px; }
.name { min-width: 0; display: flex; flex-direction: column; gap: 2px; font-size: 13px; color: var(--c-ink); }
.name span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.name small { font-size: 11px; color: var(--c-text-4); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.track { height: 8px; background: var(--c-divider); border-radius: 4px; overflow: hidden; }
.track span { display: block; height: 100%; background: var(--c-primary); border-radius: 0 4px 4px 0; min-width: 2px; }
.num { text-align: right; display: flex; flex-direction: column; gap: 2px; font-variant-numeric: tabular-nums; min-width: 64px; }
.num b { font-size: 13px; font-weight: 600; }
.num small { font-size: 11px; color: var(--c-text-4); }
</style>
