<script setup lang="ts">
withDefaults(defineProps<{
  label?: string
  detail?: string
  compact?: boolean
  /** 首次加载列表时显示的骨架行数 */
  rows?: number
}>(), { label: '正在加载…', detail: '', compact: false, rows: 0 })
</script>

<template>
  <span class="loading-state" :class="{ compact }" role="status" aria-live="polite" aria-atomic="true">
    <span class="status-line">
      <span class="spinner" aria-hidden="true" />
      <span class="copy">
        <span class="label">{{ label }}</span>
        <span v-if="detail" class="detail">{{ detail }}</span>
      </span>
    </span>
    <span v-if="rows && !compact" class="skeletons" aria-hidden="true">
      <span v-for="row in rows" :key="row" class="skeleton">
        <span class="skeleton-line short" />
        <span class="skeleton-line" />
        <span class="skeleton-line medium" />
      </span>
    </span>
  </span>
</template>

<style scoped>
.loading-state { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 24px; width: 100%; min-height: 160px; padding: 28px 20px; color: var(--c-text-3); }
.status-line { display: inline-flex; align-items: center; gap: 12px; max-width: 100%; }
.spinner { width: 24px; height: 24px; flex-shrink: 0; border: 2px solid var(--c-primary-line); border-top-color: var(--c-primary); border-radius: 50%; animation: loading-spin .85s linear infinite; }
.copy { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.label { font-size: 14px; font-weight: 500; line-height: 1.6; }
.detail { font-size: 12px; color: var(--c-text-3); line-height: 1.7; }
.compact { display: inline-flex; width: auto; min-height: 0; padding: 0; gap: 0; vertical-align: middle; color: inherit; }
.compact .status-line { gap: 8px; }
.compact .spinner { width: 14px; height: 14px; border-color: currentColor; border-top-color: transparent; }
.compact .label { font-size: inherit; font-weight: inherit; }
.skeletons { display: flex; flex-direction: column; gap: 12px; width: 100%; max-width: 680px; }
.skeleton { position: relative; display: flex; flex-direction: column; gap: 12px; padding: 20px; border: 1px solid var(--c-divider); border-radius: var(--r-md); overflow: hidden; }
.skeleton::after { content: ''; position: absolute; inset: 0; background: linear-gradient(100deg, transparent 20%, var(--c-shimmer) 50%, transparent 80%); transform: translateX(-100%); animation: loading-shimmer 1.6s ease-in-out infinite; }
.skeleton-line { display: block; height: 10px; border-radius: 5px; background: var(--c-divider); }
.skeleton-line.short { width: 28%; height: 8px; }
.skeleton-line.medium { width: 72%; }
@keyframes loading-spin { to { transform: rotate(360deg); } }
@keyframes loading-shimmer { to { transform: translateX(100%); } }
@media (prefers-reduced-motion: reduce) {
  .spinner { animation: none; }
  .skeleton::after { animation: none; display: none; }
}
</style>
