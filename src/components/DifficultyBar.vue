<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  easy: number
  mid: number
  hard: number
  height?: number
  /** 在图例中显示数量 */
  showCounts?: boolean
}>(), { height: 8, showCounts: false })

const total = computed(() => props.easy + props.mid + props.hard)
const pct = (n: number) => (total.value ? (n / total.value) * 100 : 0) + '%'
</script>

<template>
  <div class="dist" :style="{ height: height + 'px', borderRadius: height / 2 + 'px' }">
    <div class="easy" :style="{ width: pct(easy) }" />
    <div class="mid" :style="{ width: pct(mid) }" />
    <div class="hard" :style="{ width: pct(hard) }" />
  </div>
  <div class="legend" :class="{ spread: showCounts }">
    <span><b class="easy">●</b> 容易<template v-if="showCounts">{{ ' ' + easy }}</template></span>
    <span><b class="mid">●</b> 适中<template v-if="showCounts">{{ ' ' + mid }}</template></span>
    <span><b class="hard">●</b> 较难<template v-if="showCounts">{{ ' ' + hard }}</template></span>
  </div>
</template>

<style scoped>
.dist > div { transition: width .2s; }
.legend.spread { justify-content: space-between; font-size: 12px; color: var(--c-text-2); }
</style>
