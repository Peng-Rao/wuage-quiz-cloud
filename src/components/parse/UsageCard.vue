<script setup lang="ts">
import { computed, ref } from 'vue'
import type { JobUsage } from '@/api/parse'
import { PURPOSE_LABELS, formatCost, formatDuration, formatTokens, pricingNote } from '@/utils/usage'

const props = defineProps<{ usage: JobUsage | null; questionCount: number }>()

const open = ref(false)
const s = computed(() => props.usage?.summary ?? null)
const perQuestion = computed(() =>
  s.value?.cost != null && props.questionCount ? s.value.cost / props.questionCount : null,
)
const note = computed(() => (s.value ? pricingNote(s.value) : ''))
</script>

<template>
  <div class="card panel">
    <div class="head">
      <span class="card-title">AI 用量</span>
      <span v-if="s?.estimated" class="flag" title="部分调用服务端未返回用量，已按字符数估算">含估算</span>
    </div>

    <p v-if="!s || !s.calls" class="muted small">本次解析未调用大模型或 MinerU</p>
    <template v-else>
      <div class="cost">
        <span class="cost-num">{{ s.cost == null ? '—' : formatCost(s.cost, s.currency) }}</span>
        <span class="cost-label">估算费用<template v-if="perQuestion != null"> · 每题 {{ formatCost(perQuestion, s.currency) }}</template></span>
      </div>
      <p v-if="note" class="note">{{ note }}</p>

      <dl class="stats">
        <div><dt>输入 tokens</dt><dd>{{ formatTokens(s.promptTokens) }}<small v-if="s.cachedTokens">缓存 {{ formatTokens(s.cachedTokens) }}</small></dd></div>
        <div><dt>输出 tokens</dt><dd>{{ formatTokens(s.completionTokens) }}<small v-if="s.reasoningTokens">思考 {{ formatTokens(s.reasoningTokens) }}</small></dd></div>
        <div v-if="s.pages"><dt>MinerU 页数</dt><dd>{{ s.pages }} 页</dd></div>
        <div><dt>AI 耗时</dt><dd>{{ formatDuration(s.durationMs) }}</dd></div>
      </dl>
      <p v-if="s.errors" class="note warn">{{ s.errors }} 次调用失败，失败请求可能仍被计费，已计入用量</p>

      <button class="btn-link is-primary small toggle" @click="open = !open">{{ open ? '收起明细' : `调用明细（${s.calls} 次）` }}</button>
      <table v-if="open" class="calls">
        <thead><tr><th>用途</th><th>用量</th><th>费用</th></tr></thead>
        <tbody>
          <tr v-for="c in usage!.calls" :key="c.id" :class="{ failed: c.status === 'error' }">
            <td>
              {{ PURPOSE_LABELS[c.purpose] ?? c.purpose }}
              <small>{{ c.model }}<template v-if="c.status === 'error'"> · 失败</template></small>
            </td>
            <td>
              <template v-if="c.provider === 'mineru'">{{ c.pages }} 页</template>
              <template v-else>{{ formatTokens(c.promptTokens) }} / {{ formatTokens(c.completionTokens) }}<template v-if="c.estimated">*</template></template>
              <small>{{ formatDuration(c.durationMs) }}</small>
            </td>
            <td>{{ formatCost(c.cost, s.currency) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="open" class="muted small">用量列为「输入 / 输出」tokens；* 为估算值。费用按当前配置的单价计算，仅供参考，以厂商账单为准。</p>
    </template>
  </div>
</template>

<style scoped>
.panel { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; align-items: center; justify-content: space-between; }
.flag { font-size: 11px; color: #6B4E0F; background: #F8EFD9; border-radius: 4px; padding: 1px 6px; cursor: help; }
.small { font-size: 12px; margin: 0; }
.cost { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.cost-num { font-size: 26px; font-weight: 700; color: var(--c-ink); line-height: 1; font-variant-numeric: tabular-nums; }
.cost-label { font-size: 12px; color: var(--c-text-3); }
.note { margin: 0; font-size: 12px; color: var(--c-text-3); line-height: 1.6; }
.note.warn { color: #A0301F; }
.stats { margin: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 10px 12px; }
.stats div { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.stats dt { font-size: 12px; color: var(--c-text-3); }
.stats dd { margin: 0; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
.stats small, .calls small { display: block; font-size: 11px; font-weight: 400; color: var(--c-text-4); }
.toggle { align-self: flex-start; }
.calls { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
.calls th { text-align: left; font-weight: 400; color: var(--c-text-4); padding: 4px 0; border-bottom: 1px solid var(--c-divider); }
.calls td { padding: 6px 4px 6px 0; border-bottom: 1px solid var(--c-divider); vertical-align: top; }
.calls th:last-child, .calls td:last-child { text-align: right; padding-right: 0; }
.calls tr.failed td { color: #A0301F; }
</style>
