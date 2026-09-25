<script setup lang="ts">
import { computed, reactive, ref, useTemplateRef } from 'vue'
import { CN_NUM, QUESTIONS, SCORE, TYPE_ORDER } from '@/data/mock'
import { useBasketStore } from '@/stores/basket'
import ToggleSwitch from '@/components/ToggleSwitch.vue'

const basket = useBasketStore()

const opts = reactive({ showAns: false, binding: true, score: true, card: false })
const OPT_LABELS: [keyof typeof opts, string][] = [
  ['showAns', '显示答案与解析'], ['binding', '装订线'], ['score', '显示分值'], ['card', '附答题卡'],
]
const SIZES = ['A4', 'A3 双栏', 'B4'] as const
const size = ref<(typeof SIZES)[number]>('A4')

const TITLE = '2026—2027学年高一上学期期中考试'

// 试题篮为空时展示全部示例题，方便预览排版
const source = computed(() => (basket.questions.length ? basket.questions : QUESTIONS))
const sections = computed(() => {
  let no = 0
  return TYPE_ORDER
    .map((t) => ({ t, items: source.value.filter((q) => q.type === t) }))
    .filter((s) => s.items.length)
    .map((s, i) => {
      const per = SCORE[s.t], n = s.items.length
      return {
        title: `${CN_NUM[i]}、${s.t}`,
        count: n,
        score: n * per,
        heading: opts.score
          ? `${CN_NUM[i]}、${s.t}：本题共 ${n} 小题，每小题 ${per} 分，共 ${n * per} 分。`
          : `${CN_NUM[i]}、${s.t}`,
        items: s.items.map((q) => ({ ...q, no: ++no })),
      }
    })
})
const totalScore = computed(() => sections.value.reduce((a, s) => a + s.score, 0))
const totalCount = computed(() => sections.value.reduce((a, s) => a + s.count, 0))

const sheet = useTemplateRef<HTMLElement>('sheet')

function exportPdf() {
  window.print()
}

/** 以 Word 可识别的 HTML 文档导出；正式版可替换为 docx.js 生成 .docx */
function downloadWord() {
  if (!sheet.value) return
  const html = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word"><head><meta charset="utf-8"><title>${TITLE}</title>
<style>body{font-family:"宋体",serif;font-size:10.5pt;line-height:1.8}h1{font-size:16pt;text-align:center}.center{text-align:center}</style></head><body>${sheet.value.innerHTML}</body></html>`
  const blob = new Blob(['﻿', html], { type: 'application/msword' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${TITLE}数学试卷.doc`
  a.click()
  URL.revokeObjectURL(a.href)
}

function genCard() {
  opts.card = true
  requestAnimationFrame(() => document.getElementById('answer-card')?.scrollIntoView({ behavior: 'smooth' }))
}
</script>

<template>
  <main class="paper container">
    <aside class="card structure sticky-side no-print">
      <span class="card-title">试卷结构</span>
      <div v-for="s in sections" :key="s.title" class="struct-row">
        <span>{{ s.title }}</span><span class="struct-meta">{{ s.count }} 题 · {{ s.score }} 分</span>
      </div>
      <div class="total"><span>总分</span><b>{{ totalScore }} 分</b></div>
      <RouterLink to="/pick" class="more-btn">＋ 继续选题</RouterLink>
    </aside>

    <section class="sheet serif" :class="{ binding: opts.binding, a3: size === 'A3 双栏' }">
      <div ref="sheet" class="sheet-inner">
        <div class="head center">
          <div class="secret">绝密★启用前</div>
          <h1>{{ TITLE }}</h1>
          <div class="subject">数 学 试 卷</div>
          <div class="info">考试时间：120 分钟　满分：{{ totalScore }} 分</div>
          <div class="fields">
            <span>学校：__________</span><span>姓名：__________</span><span>班级：__________</span><span>考号：__________</span>
          </div>
        </div>
        <div class="notice">
          注意事项：1. 答题前填写好自己的姓名、班级、考号等信息。2. 请将答案正确填写在答题卡上。
        </div>

        <div class="body">
          <div v-for="s in sections" :key="s.title" class="part">
            <div class="part-head">{{ s.heading }}</div>
            <div v-for="q in s.items" :key="q.id" class="item">
              <div class="stem">{{ q.no }}．{{ q.stem }}</div>
              <div v-if="q.options.length" class="options">
                <span v-for="o in q.options" :key="o">{{ o }}</span>
              </div>
              <div v-if="opts.showAns" class="answer">【答案】{{ q.answer }}　【解析】{{ q.analysis }}</div>
            </div>
          </div>
        </div>

        <div v-if="opts.card" id="answer-card" class="answer-card">
          <div class="part-head">答题卡</div>
          <div class="card-grid">
            <div v-for="n in totalCount" :key="n" class="card-cell">
              <span>{{ n }}</span><span class="blank" />
            </div>
          </div>
        </div>
      </div>
    </section>

    <aside class="side sticky-side no-print">
      <div class="card settings">
        <span class="card-title">排版设置</span>
        <ToggleSwitch v-for="[k, l] in OPT_LABELS" :key="k" v-model="opts[k]" :label="l" />
        <div class="sizes-wrap">
          <span class="muted small">纸张</span>
          <div class="sizes">
            <button v-for="s in SIZES" :key="s" :class="{ on: size === s }" @click="size = s">{{ s }}</button>
          </div>
        </div>
      </div>
      <button class="btn btn-primary" @click="downloadWord">下载 Word</button>
      <button class="btn btn-outline" @click="exportPdf">导出 PDF</button>
      <button class="btn" @click="genCard">生成答题卡</button>
    </aside>
  </main>
</template>

<style scoped>
.paper { width: 100%; padding-top: 20px; padding-bottom: 48px; display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; }

.structure { flex: 0 0 220px; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.struct-row {
  display: flex; justify-content: space-between; align-items: center; padding: 8px 10px;
  background: var(--c-paper); border-radius: var(--r-sm); font-size: 13px;
}
.struct-meta { color: var(--c-text-3); }
.total { display: flex; justify-content: space-between; font-size: 13px; border-top: 1px solid var(--c-divider); padding-top: 10px; color: var(--c-text-3); }
.total b { font-weight: 700; color: var(--c-primary); }
.more-btn {
  border: 1px dashed var(--c-primary); background: #fff; color: var(--c-primary); border-radius: var(--r-sm);
  height: 34px; font-size: 13px; display: flex; align-items: center; justify-content: center;
}
.more-btn:hover { text-decoration: none; background: var(--c-primary-soft); }

/* 试卷纸面 */
.sheet {
  position: relative; background: #fff; border: 1px solid var(--c-border); border-radius: 4px;
  box-shadow: 0 10px 30px rgba(27, 36, 48, .08); padding: 56px 56px 72px; color: var(--c-ink);
  min-width: 0; flex: 999 1 520px;
}
.sheet.binding { padding-left: 88px; }
.sheet.binding::before {
  content: '装　订　线'; position: absolute; left: 28px; top: 40px; bottom: 40px; width: 20px;
  border-right: 1px dashed #C9C5BA; writing-mode: vertical-rl; display: flex; align-items: center; justify-content: center;
  font-size: 11px; letter-spacing: 8px; color: var(--c-text-4); font-family: var(--font-sans);
}
.sheet-inner { display: flex; flex-direction: column; gap: 18px; }
.center { text-align: center; }
.head { display: flex; flex-direction: column; gap: 10px; }
.secret { font-size: 12px; letter-spacing: 4px; color: var(--c-text-3); }
.head h1 { margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 1px; }
.subject { font-size: 20px; font-weight: 600; letter-spacing: 6px; }
.info { font-size: 13px; color: var(--c-text-2); }
.fields { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px 28px; font-size: 13px; color: var(--c-text-2); padding-top: 4px; }
.notice { border: 1px solid #D8D4C8; padding: 10px 14px; font-size: 13px; line-height: 1.8; color: var(--c-text-2); }

.body { display: flex; flex-direction: column; gap: 18px; }
.a3 .body { display: block; column-count: 2; column-gap: 40px; column-rule: 1px dashed var(--c-border); }
.a3 .part { break-inside: avoid-column; margin-bottom: 18px; }
.part { display: flex; flex-direction: column; gap: 14px; }
.part-head { font-size: 15.5px; font-weight: 700; font-family: var(--font-sans); }
.item { display: flex; flex-direction: column; gap: 6px; font-size: 15px; line-height: 1.9; border-radius: 4px; }
.item:hover { background: #FBFAF6; }
.stem { text-wrap: pretty; }
.options { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0 20px; padding-left: 1.5em; }
.answer { font-size: 13px; color: var(--c-primary); font-family: var(--font-sans); padding-left: 1.5em; line-height: 1.7; }

.answer-card { border-top: 2px solid var(--c-ink); padding-top: 16px; display: flex; flex-direction: column; gap: 12px; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.card-cell { display: flex; align-items: center; gap: 8px; font-size: 13px; font-family: var(--font-sans); }
.card-cell .blank { flex: 1; height: 22px; border: 1px solid #C9C5BA; border-radius: 3px; }

/* 右侧设置 */
.side { flex: 1 0 260px; display: flex; flex-direction: column; gap: 14px; }
.settings { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
.sizes-wrap { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid var(--c-divider); padding-top: 12px; }
.small { font-size: 12px; }
.sizes { display: flex; gap: 6px; }
.sizes button {
  flex: 1; padding: 5px 0; font-size: 13px; border: none; border-radius: var(--r-sm);
  background: var(--c-paper); color: var(--c-text-2);
}
.sizes button.on { background: var(--c-primary); color: #fff; }

@media (max-width: 800px) {
  .sticky-side { position: static; }
  .structure { flex: 1 1 100%; }
  .sheet { padding: 32px 20px 40px; }
  .sheet.binding { padding-left: 52px; }
  .sheet.binding::before { left: 12px; }
  .a3 .body { column-count: 1; }
}

@media print {
  .paper { padding: 0; display: block; max-width: none; }
  .sheet { border: none; box-shadow: none; padding: 0 0 0 48px; }
  .sheet:not(.binding) { padding-left: 0; }
  .sheet.binding::before { left: 0; top: 0; bottom: 0; }
  .item:hover { background: none; }
}
</style>

<style>
@media print {
  .no-print, header, footer { display: none !important; }
  body { background: #fff; }
}
</style>
