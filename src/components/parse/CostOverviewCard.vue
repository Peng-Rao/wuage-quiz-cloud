<script setup lang="ts">
import { computed } from 'vue'
import type { UsageOverview } from '@/api/parse'
import { formatCost, formatTokens, pricingNote } from '@/utils/usage'

const props = defineProps<{ overview: UsageOverview | null }>()

const o = computed(() => props.overview)
const cur = computed(() => o.value?.summary.currency ?? '¥')
const note = computed(() => (o.value?.summary.calls ? pricingNote(o.value.summary) : ''))
</script>

<template>
  <div class="card panel">
    <span class="card-title">AI 成本<small v-if="o">近 {{ o.days }} 天</small></span>
    <p v-if="!o || !o.jobs" class="muted small">还没有解析记录，上传试卷后这里会给出每份试卷的平均成本</p>
    <template v-else>
      <div class="avg">
        <span class="avg-num">{{ o.costPerJob == null ? formatTokens(o.tokensPerJob ?? 0) : formatCost(o.costPerJob, cur) }}</span>
        <span class="avg-label">{{ o.costPerJob == null ? 'tokens / 份（未配置单价）' : '平均每份试卷' }}</span>
      </div>
      <dl class="stats">
        <div><dt>解析试卷</dt><dd>{{ o.jobs }} 份 · {{ o.questions }} 题</dd></div>
        <div><dt>合计费用</dt><dd>{{ formatCost(o.summary.cost, cur) }}</dd></div>
        <div><dt>每页</dt><dd>{{ formatCost(o.costPerPage, cur) }}</dd></div>
        <div><dt>每题</dt><dd>{{ formatCost(o.costPerQuestion, cur) }}</dd></div>
      </dl>
      <p v-if="note" class="note">{{ note }}</p>
    </template>
  </div>
</template>

<style scoped>
.panel { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
.card-title small { font-size: 12px; font-weight: 400; color: var(--c-text-4); margin-left: 8px; }
.small { font-size: 12px; margin: 0; line-height: 1.6; }
.avg { display: flex; align-items: baseline; gap: 8px; }
.avg-num { font-size: 24px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
.avg-label { font-size: 12px; color: var(--c-text-3); }
.stats { margin: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 10px 12px; }
.stats div { display: flex; flex-direction: column; gap: 2px; }
.stats dt { font-size: 12px; color: var(--c-text-3); }
.stats dd { margin: 0; font-size: 13.5px; font-weight: 600; font-variant-numeric: tabular-nums; }
.note { margin: 0; font-size: 12px; color: var(--c-text-3); line-height: 1.6; }
</style>
