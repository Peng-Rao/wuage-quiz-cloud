<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { request } from '@/api/request'
import { storeToRefs } from 'pinia'
import { CN_NUM } from '@/data/mock'
import type { QuestionType } from '@/api/parse'
import { useAppStore } from '@/stores/app'
import { useBasketStore } from '@/stores/basket'
import MathText from '@/components/MathText.vue'
import { plainText } from '@/utils/math'
import ToggleSwitch from '@/components/ToggleSwitch.vue'
import { noSep, optionCols, optionLabel, provideSubject } from '@/utils/subject'

const basket = useBasketStore()
const { subject } = storeToRefs(useAppStore())
/** 试卷按当前学科排版：语文、英语的阅读材料、诗文、对话等按卷面习惯显示 */
const layout = provideSubject(subject)

const opts = reactive({ showAns: false, binding: true, score: true, card: false })
const OPT_LABELS: [keyof typeof opts, string][] = [
  ['showAns', '显示答案与解析'], ['binding', '装订线'], ['score', '显示分值'], ['card', '附答题卡'],
]
const SIZES = ['A4', 'A3 双栏', 'B4'] as const
const size = ref<(typeof SIZES)[number]>('A4')

const TITLE = '2026—2027学年高一上学期期中考试'
const subjectTitle = computed(() => `${[...subject.value].join(' ')} 试 卷`)

/** 大题：按试题篮中的大题顺序与题序连续编号 */
const sections = computed(() => {
  let no = 0
  return basket.sections.map((s, i) => {
    const n = s.items.length
    const heading = !opts.score
      ? `${CN_NUM[i]}、${s.type}`
      : s.each !== null
        ? `${CN_NUM[i]}、${s.type}：本题共 ${n} 小题，每小题 ${s.each} 分，共 ${s.score} 分。`
        : `${CN_NUM[i]}、${s.type}：本题共 ${n} 小题，共 ${s.score} 分。`
    return { ...s, title: `${CN_NUM[i]}、${s.type}`, heading, items: s.items.map((x) => ({ ...x, no: ++no })) }
  })
})

async function validateBasket() {
  await request('POST', '/api/basket/validate', { ids: basket.items.map(item => item.q.id) })
}
async function exportPdf() {
  exportError.value = ''
  try { await validateBasket(); window.print() }
  catch (e) { exportError.value = (e as Error).message }
}

/** 导出 .docx：公式为 Word 原生公式，题目配图嵌入文档（生成器按需加载） */
const exporting = ref(false)
const exportError = ref('')
async function downloadWord() {
  if (!basket.count || exporting.value) return
  exporting.value = true
  exportError.value = ''
  try {
    await validateBasket()
    const { buildDocx } = await import('@/utils/docx')
    const blob = await buildDocx({
      secret: '绝密★启用前', title: TITLE, subtitle: subjectTitle.value,
      info: `考试时间：120 分钟　满分：${basket.totalScore} 分`,
      fields: ['学校：__________', '姓名：__________', '班级：__________', '考号：__________'],
      notice: '注意事项：1. 答题前填写好自己的姓名、班级、考号等信息。2. 请将答案正确填写在答题卡上。',
      sections: sections.value.map((s) => ({
        heading: s.heading,
        items: s.items.map((it) => ({
          no: it.no, scoreMark: opts.score && s.each === null ? `（${it.score} 分）` : '', stem: it.q.stem,
          options: it.q.options, images: it.q.images, answer: it.q.answer, analysis: it.q.analysis,
        })),
      })),
      showAnswer: opts.showAns, answerCard: opts.card ? basket.count : 0, size: size.value, binding: opts.binding,
      layout: layout.value,
    })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${TITLE}${subject.value}试卷.docx`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  } catch (e) {
    exportError.value = `导出失败：${(e as Error).message}`
  } finally {
    exporting.value = false
  }
}

function genCard() {
  opts.card = true
  requestAnimationFrame(() => document.getElementById('answer-card')?.scrollIntoView({ behavior: 'smooth' }))
}

// ---------- 结构编辑：题序与分值 ----------

/** 结构行摘要：独立公式转行内、压成一行，交给 MathText 渲染（行高受限，放不下独立公式） */
const brief = (stem: string) => stem.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex: string) => `$${tex}$`).replace(/\s+/g, ' ').trim()
const num = (e: Event) => (e.target as HTMLInputElement).valueAsNumber

/** 同一大题内拖动排序 */
const drag = ref<{ type: QuestionType; from: number } | null>(null)
const over = ref<string | null>(null)
function onDrop(type: QuestionType, to: number) {
  if (drag.value && drag.value.type === type) basket.reorder(type, drag.value.from, to)
  drag.value = null
  over.value = null
}
function onDragOver(e: DragEvent, type: QuestionType, id: string) {
  if (drag.value?.type !== type) return
  e.preventDefault()
  over.value = id
}

/** 在试卷上点击题目时定位到结构中的对应行 */
const focusId = ref<string | null>(null)
function focusRow(id: string) {
  focusId.value = id
  document.getElementById(`row-${id}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}
</script>

<template>
  <main class="paper container">
    <aside class="card structure sticky-side no-print">
      <div class="struct-head">
        <span class="card-title">试卷结构</span>
        <button v-if="basket.count" class="btn-link small" title="各题分值恢复为原卷分值" @click="basket.resetScores()">恢复原卷分值</button>
      </div>
      <p v-if="basket.count" class="struct-tip">拖动或用 ▲▼ 调整题序；修改分值后试卷即时更新。</p>

      <div class="struct-body">
        <section v-for="(s, si) in sections" :key="s.type" class="struct-sec">
          <div class="sec-head">
            <span class="sec-title">{{ s.title }}</span>
            <span class="sec-meta">{{ s.items.length }} 题 · {{ s.score }} 分</span>
            <span class="sec-move">
              <button :disabled="si === 0" :aria-label="`${s.title}上移`" @click="basket.moveSection(s.type, -1)">▲</button>
              <button :disabled="si === sections.length - 1" :aria-label="`${s.title}下移`" @click="basket.moveSection(s.type, 1)">▼</button>
            </span>
          </div>
          <label class="sec-each">
            每小题
            <input
              type="number" min="0" max="200" step="0.5" :value="s.each ?? ''" placeholder="不同"
              :aria-label="`${s.title}每小题分值`" @change="basket.setSectionScore(s.type, num($event))"
            >
            分
          </label>
          <ol class="rows">
            <li
              v-for="(it, i) in s.items" :id="`row-${it.q.id}`" :key="it.q.id" class="row"
              :class="{ over: over === it.q.id, focus: focusId === it.q.id }" draggable="true"
              @dragstart="drag = { type: s.type, from: i }" @dragend="drag = null; over = null"
              @dragover="onDragOver($event, s.type, it.q.id)" @dragleave="over = null" @drop.prevent="onDrop(s.type, i)"
            >
              <span class="grip" aria-hidden="true">⋮⋮</span>
              <span class="row-no">{{ it.no }}</span>
              <span class="row-stem" :title="plainText(it.q.stem)"><MathText :text="brief(it.q.stem)" :subject="null" /></span>
              <input
                class="row-score" type="number" min="0" max="200" step="0.5" :value="it.score"
                :aria-label="`第 ${it.no} 题分值`" @change="basket.setScore(it.q.id, num($event))"
              ><span class="unit">分</span>
              <span class="row-ops">
                <button :disabled="i === 0" :aria-label="`第 ${it.no} 题上移`" @click="basket.move(it.q.id, -1)">▲</button>
                <button :disabled="i === s.items.length - 1" :aria-label="`第 ${it.no} 题下移`" @click="basket.move(it.q.id, 1)">▼</button>
                <button class="del" :aria-label="`移出第 ${it.no} 题`" @click="basket.remove(it.q.id)">×</button>
              </span>
            </li>
          </ol>
        </section>
        <p v-if="!basket.count" class="struct-empty">试题篮是空的。</p>
      </div>

      <div class="total"><span>共 {{ basket.count }} 题 · 总分</span><b>{{ basket.totalScore }} 分</b></div>
      <div class="more">
        <RouterLink to="/chapter" class="more-btn">＋ 继续选题</RouterLink>
        <RouterLink to="/papers" class="more-btn">从试卷选题添加</RouterLink>
      </div>
    </aside>

    <section class="sheet serif" :class="[`lay-${layout}`, { binding: opts.binding, a3: size === 'A3 双栏' }]">
      <div v-if="!basket.count" class="sheet-empty no-print">
        <p>试题篮还是空的，先去挑选题目吧。</p>
        <div class="empty-links">
          <RouterLink to="/chapter" class="btn btn-primary">去选题</RouterLink>
          <RouterLink to="/papers" class="btn btn-outline">从试卷选题整卷组卷</RouterLink>
        </div>
      </div>
      <div v-else class="sheet-inner">
        <div class="head center">
          <div class="secret">绝密★启用前</div>
          <h1>{{ TITLE }}</h1>
          <div class="subject">{{ subjectTitle }}</div>
          <div class="info">考试时间：120 分钟　满分：{{ basket.totalScore }} 分</div>
          <div class="fields">
            <span>学校：__________</span><span>姓名：__________</span><span>班级：__________</span><span>考号：__________</span>
          </div>
        </div>
        <div class="notice">
          注意事项：1. 答题前填写好自己的姓名、班级、考号等信息。2. 请将答案正确填写在答题卡上。
        </div>

        <div class="body">
          <div v-for="s in sections" :key="s.type" class="part">
            <div class="part-head">{{ s.heading }}</div>
            <div v-for="it in s.items" :key="it.q.id" class="item" @click="focusRow(it.q.id)">
              <div class="stem">
                {{ it.no }}{{ noSep(layout) }}<template v-if="opts.score && s.each === null">（{{ it.score }} 分）</template><MathText :text="it.q.stem" />
              </div>
              <div v-if="it.q.images.length" class="images">
                <img v-for="src in it.q.images" :key="src" :src="src" alt="题目配图">
              </div>
              <div v-if="it.q.options.length" class="options" :class="layout !== 'plain' && ['opt-fixed', `cols-${optionCols(it.q.options)}`]">
                <span v-for="(o, i) in it.q.options" :key="i">{{ optionLabel(layout, i) }}<MathText :text="o" /></span>
              </div>
              <div v-if="opts.showAns" class="answer">
                【答案】<MathText :text="it.q.answer || '略'" /><template v-if="it.q.analysis">　【解析】<MathText :text="it.q.analysis" /></template>
              </div>
            </div>
          </div>
        </div>

        <div v-if="opts.card" id="answer-card" class="answer-card">
          <div class="part-head">答题卡</div>
          <div class="card-grid">
            <div v-for="n in basket.count" :key="n" class="card-cell">
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
      <button class="btn btn-primary" :disabled="!basket.count || exporting" @click="downloadWord">{{ exporting ? '生成中…' : '下载 Word' }}</button>
      <p v-if="exportError" class="export-err">{{ exportError }}</p>
      <button class="btn btn-outline" :disabled="!basket.count" @click="exportPdf">导出 PDF</button>
      <button class="btn" :disabled="!basket.count" @click="genCard">生成答题卡</button>
    </aside>
  </main>
</template>

<style scoped>
.paper { width: 100%; padding-top: 20px; padding-bottom: 48px; display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; }

/* 左侧结构编辑 */
.structure { flex: 0 0 320px; min-width: 0; padding: 16px; display: flex; flex-direction: column; gap: 10px; max-height: calc(100vh - var(--sticky-top) - 20px); }
.struct-head { display: flex; align-items: baseline; justify-content: space-between; }
.small { font-size: 12px; }
.struct-tip { margin: 0; font-size: 12px; color: var(--c-text-4); line-height: 1.6; }
.struct-body { display: flex; flex-direction: column; gap: 12px; overflow: auto; margin: 0 -6px; padding: 0 6px; }
.struct-sec { display: flex; flex-direction: column; gap: 6px; }
.sec-head {
  display: flex; align-items: center; gap: 8px; padding: 7px 8px 7px 10px;
  background: var(--c-paper); border-radius: var(--r-sm); font-size: 13px;
}
.sec-title { font-weight: 600; }
.sec-meta { color: var(--c-text-3); margin-left: auto; font-size: 12px; }
.sec-move, .row-ops { display: flex; gap: 2px; }
.sec-move button, .row-ops button {
  width: 22px; height: 22px; border: none; background: transparent; border-radius: 4px; font-size: 9px; color: var(--c-text-3); padding: 0;
}
.sec-move button:hover:not(:disabled), .row-ops button:hover:not(:disabled) { background: #fff; color: var(--c-primary); }
.sec-move button:disabled, .row-ops button:disabled { opacity: .3; cursor: default; }
.row-ops .del { font-size: 14px; }
.row-ops .del:hover { color: var(--c-hard) !important; }
.sec-each { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-text-3); padding-left: 10px; }
.sec-each input, .row-score {
  width: 52px; height: 24px; border: 1px solid var(--c-border); border-radius: 4px; padding: 0 4px;
  font-size: 12px; text-align: center; color: var(--c-ink); font-family: inherit;
}
.sec-each input:focus, .row-score:focus { outline: none; border-color: var(--c-primary); }
.rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.row {
  display: flex; align-items: center; gap: 6px; padding: 4px 4px 4px 2px; border-radius: var(--r-sm);
  font-size: 12px; border: 1px solid transparent; background: #fff; cursor: grab;
}
.row:hover { background: var(--c-surface-2); border-color: var(--c-divider); }
.row.over { border-color: var(--c-primary); border-style: dashed; }
.row.focus { background: var(--c-primary-soft); }
.grip { color: var(--c-text-4); font-size: 10px; letter-spacing: -2px; width: 10px; }
.row-no { width: 20px; text-align: right; color: var(--c-text-3); flex-shrink: 0; }
.row-stem {
  flex: 1; min-width: 0; overflow: hidden; color: var(--c-text-2); line-height: 1.45;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow-wrap: anywhere;
}
.row-score { width: 44px; flex-shrink: 0; }
.unit { color: var(--c-text-4); margin-left: -3px; }
.struct-empty { margin: 0; padding: 12px 4px; font-size: 13px; color: var(--c-text-4); }
.total { display: flex; justify-content: space-between; font-size: 13px; border-top: 1px solid var(--c-divider); padding-top: 10px; color: var(--c-text-3); }
.total b { font-weight: 700; color: var(--c-primary); }
.more { display: flex; gap: 8px; }
.more-btn {
  flex: 1; border: 1px dashed var(--c-primary); background: #fff; color: var(--c-primary); border-radius: var(--r-sm);
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
.sheet-empty { min-height: 360px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px; font-family: var(--font-sans); color: var(--c-text-3); }
.sheet-empty p { margin: 0; font-size: 15px; }
.empty-links { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }
.empty-links .btn { padding: 0 18px; display: inline-flex; align-items: center; }
.empty-links .btn:hover { text-decoration: none; }
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
.item { display: flex; flex-direction: column; gap: 6px; font-size: 15px; line-height: 1.9; border-radius: 4px; cursor: pointer; }
.item:hover { background: #FBFAF6; }
.stem { text-wrap: pretty; }
.images { display: flex; flex-wrap: wrap; gap: 10px; padding-left: 1.5em; }
.images img { max-width: min(100%, 320px); max-height: 220px; object-fit: contain; }
.options { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0 20px; padding-left: 1.5em; }
.answer { font-size: 13px; color: var(--c-primary); font-family: var(--font-sans); padding-left: 1.5em; line-height: 1.7; }

.answer-card { border-top: 2px solid var(--c-ink); padding-top: 16px; display: flex; flex-direction: column; gap: 12px; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.card-cell { display: flex; align-items: center; gap: 8px; font-size: 13px; font-family: var(--font-sans); }
.card-cell .blank { flex: 1; height: 22px; border: 1px solid #C9C5BA; border-radius: 3px; }

/* 右侧设置 */
.side { flex: 1 0 240px; display: flex; flex-direction: column; gap: 14px; }
.side .btn:disabled { opacity: .5; cursor: default; }
.export-err { margin: -6px 0 0; font-size: 12px; color: var(--c-hard); }
.settings { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
.sizes-wrap { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid var(--c-divider); padding-top: 12px; }
.sizes { display: flex; gap: 6px; }
.sizes button {
  flex: 1; padding: 5px 0; font-size: 13px; border: none; border-radius: var(--r-sm);
  background: var(--c-paper); color: var(--c-text-2);
}
.sizes button.on { background: var(--c-primary); color: #fff; }

@media (max-width: 800px) {
  .sticky-side { position: static; }
  .structure { flex: 1 1 100%; max-height: none; }
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
