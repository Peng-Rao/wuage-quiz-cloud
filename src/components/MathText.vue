<script setup lang="ts">
import { computed } from 'vue'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { splitMath } from '@/utils/math'

/** 渲染夹带公式的文本：$...$ 为行内公式，$$...$$ 为独立公式（MinerU 输出格式） */
const props = defineProps<{ text: string }>()

const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)

const html = computed(() =>
  splitMath(props.text).map((s) => (s.math
    ? katex.renderToString(s.text, { throwOnError: false, displayMode: s.display, output: 'html' })
    : escapeHtml(s.text))).join(''),
)
</script>

<template>
  <!-- 文本已转义，公式由 KaTeX 生成 -->
  <span class="math-text" v-html="html" />
</template>

<style scoped>
.math-text { white-space: pre-line; }
.math-text :deep(.katex-display) { margin: 6px 0; overflow-x: auto; overflow-y: hidden; }
</style>
