<script setup lang="ts">
import { computed, ref } from 'vue'
import MathText from '@/components/MathText.vue'
import { plainText } from '@/utils/math'

/**
 * 阅读材料：英语阅读 / 完形填空原文、语文选文等，同一篇材料下的各题共用，显示在题干上方。
 * 按外层 provideSubject 的学科排版（段落缩进、标题居中等）；较长的材料先折叠。
 */
const props = withDefaults(defineProps<{
  text: string
  /** 初始是否收起：collapsed 只显示标题行，folded 显示开头几行，open 全文 */
  initial?: 'collapsed' | 'folded' | 'open'
}>(), { initial: 'folded' })

const length = computed(() => plainText(props.text).length)
const long = computed(() => length.value > 240)
const state = ref(props.initial === 'folded' && !long.value ? 'open' : props.initial)
const next = () => { state.value = state.value === 'open' ? (long.value ? 'folded' : 'collapsed') : 'open' }
</script>

<template>
  <section class="material" :class="state">
    <button type="button" class="m-head" :aria-expanded="state === 'open'" @click="next">
      <span class="m-tag">阅读材料</span>
      <span class="m-len">{{ length }} 字</span>
      <span class="m-act">{{ state === 'open' ? '收起 ▴' : '展开全文 ▾' }}</span>
    </button>
    <div v-if="state !== 'collapsed'" class="m-body serif"><MathText :text="text" block /></div>
  </section>
</template>

<style scoped>
.material { border-left: 3px solid var(--c-primary-line); background: var(--c-surface-2); border-radius: 0 var(--r-sm) var(--r-sm) 0; }
.m-head {
  display: flex; align-items: center; gap: 10px; width: 100%; padding: 6px 12px; border: none; background: none;
  font-family: var(--font-sans); font-size: 12px; color: var(--c-text-3); text-align: left; cursor: pointer;
}
.m-tag { color: var(--c-primary-dark); font-weight: 600; }
.m-act { margin-left: auto; color: var(--c-primary); }
.m-body { padding: 0 14px 10px; font-size: 15px; line-height: 1.85; color: var(--c-ink); }
.folded .m-body {
  max-height: 9.5em; overflow: hidden;
  -webkit-mask-image: linear-gradient(#000 60%, transparent); mask-image: linear-gradient(#000 60%, transparent);
}
</style>
