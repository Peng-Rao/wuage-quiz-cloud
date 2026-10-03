<script setup lang="ts">
import { computed } from 'vue'
import 'katex/dist/katex.min.css'
import { splitMath } from '@/utils/math'
import { renderRich, renderTex } from '@/utils/richtext'
import { layoutOf, useTextLayout } from '@/utils/subject'

/**
 * 渲染夹带公式的文本：$...$ 为行内公式，$$...$$ 为独立公式（MinerU 输出格式）。
 * 语文、英语按卷面结构排版（段落缩进、诗词居中、对话分行、填空横线等），学科取自 subject 或外层 provideSubject。
 */
const props = defineProps<{
  text: string
  subject?: string | null
  /** 独立成块（阅读材料）：首行不接在题号后，正文段落都缩进 */
  block?: boolean
}>()
const injected = useTextLayout()
const layout = computed(() => (props.subject !== undefined ? layoutOf(props.subject) : injected.value))

const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)

const html = computed(() => (layout.value === 'plain'
  ? splitMath(props.text).map((s) => (s.math ? renderTex(s.text, s.display) : escapeHtml(s.text))).join('')
  : renderRich(props.text, layout.value, props.block)))
</script>

<template>
  <!-- 文本已转义，公式由 KaTeX 生成 -->
  <span
    class="math-text" :class="layout !== 'plain' && ['rt', `rt-${layout}`]"
    :lang="layout === 'en' ? 'en' : undefined" v-html="html"
  />
</template>

<style scoped>
.math-text { white-space: pre-line; }
.math-text :deep(.katex-display) { margin: 6px 0; overflow-x: auto; overflow-y: hidden; }

/* ---------- 语文、英语 ---------- */
.rt { white-space: normal; }
.rt :deep(.ln) { display: block; }
.rt :deep(.ln.lead) { display: inline; padding-left: 0; text-indent: 0; }
.rt :deep(.ln + .ln) { margin-top: .15em; }
.rt :deep(.ln-para) { text-indent: 2em; }
.rt :deep(.ln-sub), .rt :deep(.ln-turn) { padding-left: 2em; text-indent: -2em; }
.rt :deep(.ln-title) { text-align: center; font-weight: 700; margin: .3em 0 .1em; }
.rt :deep(.ln-byline) { text-align: center; font-size: .9em; color: var(--c-text-2); }
.rt :deep(.ln-display) { text-align: center; }
/* 行内元素不继承首行缩进 */
.rt :deep(.katex), .rt :deep(.blank), .rt :deep(.ipa) { text-indent: 0; }
.rt :deep(.blank) {
  display: inline-block; height: 1.15em; margin: 0 .2em; vertical-align: -0.2em;
  border-bottom: 1px solid currentColor; text-align: center; line-height: 1.15em;
}
.rt :deep(.blank.no) { font-family: var(--font-en); font-size: .9em; }

.rt-zh { text-align: justify; }
.rt-zh :deep(.ln-verse) { text-align: center; font-family: var(--font-kai); letter-spacing: .06em; }
.rt-zh :deep(.ln-title) { font-family: var(--font-sans); letter-spacing: .08em; }

.rt-en { font-family: var(--font-en); hyphens: auto; overflow-wrap: break-word; }
/* 对话：后续各行与题号后的第一句对齐，折行再缩进 */
.rt-en :deep(.ln-turn) { padding-left: 2em; text-indent: -1em; }
.rt-en :deep(.ipa) { font-family: var(--font-ipa); font-size: .95em; }
</style>
