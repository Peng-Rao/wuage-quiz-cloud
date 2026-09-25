<script setup lang="ts">
import { computed } from 'vue'
import katex from 'katex'
import 'katex/dist/katex.min.css'

/** 渲染夹带公式的文本：$...$ 为行内公式，$$...$$ 为独立公式（MinerU 输出格式） */
const props = defineProps<{ text: string }>()

const MATH = /(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g

const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)

const html = computed(() =>
  props.text.split(MATH).map((part, i) => {
    // split 带捕获组时，奇数位是公式
    if (i % 2 === 0) return escapeHtml(part)
    const display = part.startsWith('$$')
    const tex = part.slice(display ? 2 : 1, display ? -2 : -1)
    return katex.renderToString(tex, { throwOnError: false, displayMode: display, output: 'html' })
  }).join(''),
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
