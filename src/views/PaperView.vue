<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, watchEffect } from 'vue'
import { request } from '@/api/request'
import { storeToRefs } from 'pinia'
import { CN_NUM } from '@/data/mock'
import type { QuestionType } from '@/api/parse'
import { useAppStore } from '@/stores/app'
import { useBasketStore } from '@/stores/basket'
import MathText from '@/components/MathText.vue'
import PaperBlock, { type PaperBlockData } from '@/components/PaperBlock.vue'
import { plainText } from '@/utils/math'
import ToggleSwitch from '@/components/ToggleSwitch.vue'
import { provideSubject } from '@/utils/subject'
import { MM, pageBox, paginate, type PaperSize } from '@/utils/paginate'

const basket = useBasketStore()
const { subject } = storeToRefs(useAppStore())
/** 试卷按当前学科排版：语文、英语的阅读材料、诗文、对话等按卷面习惯显示 */
const layout = provideSubject(subject)

const opts = reactive({ showAns: false, binding: true, score: true, card: false })
const OPT_LABELS: [keyof typeof opts, string][] = [
  ['showAns', '显示答案与解析'], ['binding', '装订线'], ['score', '显示分值'], ['card', '附答题卡'],
]
const SIZES: PaperSize[] = ['A4', 'A3 双栏', 'B4']
const size = ref<PaperSize>('A4')

/** 卷头在试卷上直接修改，存于试题篮，换页面回来仍在 */
const head = computed(() => basket.head)
const autoSubtitle = computed(() => `${[...subject.value].join(' ')} 试 卷`)
const subjectTitle = computed(() => head.value.subtitle ?? autoSubtitle.value)

/** 大题：按试题篮中的大题顺序与题序连续编号；同一篇阅读材料下相邻的题，材料只在第一题前印一次 */
const sections = computed(() => {
  let no = 0
  return basket.sections.map((s, i) => {
    const n = s.items.length
    const heading = !opts.score
      ? `${CN_NUM[i]}、${s.type}`
      : s.each !== null
        ? `${CN_NUM[i]}、${s.type}：本题共 ${n} 小题，每小题 ${s.each} 分，共 ${s.score} 分。`
        : `${CN_NUM[i]}、${s.type}：本题共 ${n} 小题，共 ${s.score} 分。`
    return {
      ...s, title: `${CN_NUM[i]}、${s.type}`, heading,
      items: s.items.map((x, j) => ({
        ...x, no: ++no, material: x.q.material && x.q.material !== s.items[j - 1]?.q.material ? x.q.material : null,
      })),
    }
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
      secret: head.value.secret, title: head.value.title, subtitle: subjectTitle.value,
      info: [head.value.duration.trim(), `满分：${basket.totalScore} 分`].filter(Boolean).join('　'),
      fields: head.value.fields.filter((f) => f.trim()).map((f) => `${f.trim()}：__________`),
      notice: head.value.notice,
      sections: sections.value.map((s) => ({
        heading: s.heading,
        items: s.items.map((it) => ({
          no: it.no, scoreMark: opts.score && s.each === null ? `（${it.score} 分）` : '', stem: it.q.stem,
          material: it.material,
          options: it.q.options, images: it.q.images, answer: it.q.answer, analysis: it.q.analysis,
        })),
      })),
      showAnswer: opts.showAns, answerCard: opts.card ? basket.count : 0, size: size.value, binding: opts.binding,
      layout: layout.value,
    })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${head.value.title.trim() || '试卷'}${subject.value}试卷.docx`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  } catch (e) {
    exportError.value = `导出失败：${(e as Error).message}`
  } finally {
    exporting.value = false
  }
}

async function genCard() {
  opts.card = true
  await nextTick()
  requestAnimationFrame(() => document.getElementById('answer-card')?.scrollIntoView({ behavior: 'smooth' }))
}

// ---------- 自动分页 ----------

/** 卷面依次排列的块；gap 为与上一块的间距（像素） */
const blocks = computed<PaperBlockData[]>(() => {
  const out: PaperBlockData[] = [{ key: 'head', kind: 'head', gap: 0 }, { key: 'notice', kind: 'notice', gap: 18 }]
  sections.value.forEach((s, si) => {
    out.push({ key: `part-${s.type}`, kind: 'part', heading: s.heading, gap: si ? 26 : 22, keepWithNext: true })
    s.items.forEach((it, j) => {
      const gap = j ? 16 : 10
      if (it.material) out.push({ key: `mat-${it.q.id}`, kind: 'material', text: it.material, gap, keepWithNext: true })
      out.push({
        key: it.q.id, kind: 'item', q: it.q, no: it.no, gap: it.material ? 6 : gap,
        scoreMark: opts.score && s.each === null ? `（${it.score} 分）` : '',
      })
    })
  })
  if (opts.card) out.push({ key: 'card', kind: 'card', gap: 28 })
  return out
})

const box = computed(() => pageBox(size.value, opts.binding))
/** 各栏中的块 key；由隐藏的测量副本量出每块高度后装栏 */
const columns = ref<string[][]>([])
const pages = computed(() => {
  const byKey = new Map(blocks.value.map((b) => [b.key, b]))
  const cols = columns.value.map((c) => c.map((k) => byKey.get(k)).filter((b): b is PaperBlockData => !!b))
  const out: PaperBlockData[][][] = []
  for (let i = 0; i < cols.length; i += box.value.cols) {
    const page = cols.slice(i, i + box.value.cols)
    while (page.length < box.value.cols) page.push([])
    out.push(page)
  }
  return out
})

const measureEl = ref<HTMLElement | null>(null)
function repaginate() {
  const el = measureEl.value
  if (!el) return
  const heights = [...el.children].map((c) => c.getBoundingClientRect().height)
  if (heights.length !== blocks.value.length) return
  const next = paginate(blocks.value, heights, box.value.colHeight)
  if (JSON.stringify(next) !== JSON.stringify(columns.value)) columns.value = next
  observeBlocks()
}

/** 公式、配图、字体加载后块高度会变，随时重新分页 */
let frame = 0
const schedule = () => {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(repaginate)
}
const resizeObs = new ResizeObserver(schedule)
const observed = new Set<Element>()
function observeBlocks() {
  const now = new Set(measureEl.value?.children ?? [])
  for (const c of observed) if (!now.has(c)) { resizeObs.unobserve(c); observed.delete(c) }
  for (const c of now) if (!observed.has(c)) { resizeObs.observe(c); observed.add(c) }
}
watch([blocks, box, () => opts.showAns], repaginate, { flush: 'post' })

/** 纸张按实际尺寸排版，整体缩放到中间栏的宽度 */
const sheetEl = ref<HTMLElement | null>(null)
const pagesEl = ref<HTMLElement | null>(null)
const view = reactive({ width: 0, height: 0 })
const pageWidth = computed(() => box.value.w * MM)
const scale = computed(() => (view.width ? Math.min(1, view.width / pageWidth.value) : 1))
const viewObs = new ResizeObserver(() => {
  view.width = sheetEl.value?.clientWidth ?? 0
  view.height = pagesEl.value?.offsetHeight ?? 0
})
watch([sheetEl, pagesEl], ([a, b], [oa, ob]) => {
  for (const el of [oa, ob]) if (el) viewObs.unobserve(el)
  for (const el of [a, b]) if (el) viewObs.observe(el)
})

/** 打印 / 导出 PDF 时的纸张大小 */
const pageStyle = document.createElement('style')
watchEffect(() => { pageStyle.textContent = `@page { size: ${box.value.w}mm ${box.value.h}mm; margin: 0; }` })
onMounted(() => {
  document.head.append(pageStyle)
  repaginate()
})
onBeforeUnmount(() => {
  cancelAnimationFrame(frame)
  resizeObs.disconnect()
  viewObs.disconnect()
  pageStyle.remove()
})

// ---------- 结构编辑：题序与分值 ----------

/** 结构行摘要：独立公式转行内、压成一行，交给 MathText 渲染（行高受限，放不下独立公式） */
const brief = (stem: string) => stem.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex: string) => `$${tex}$`).replace(/\s+/g, ' ').trim()
/** 结构行文字：完形填空等题题干为空，用选项代替 */
const rowText = (q: { stem: string; options: string[] }) => q.stem.trim() || q.options.join(' / ')
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
              <span class="row-stem" :title="plainText(rowText(it.q))"><MathText :text="brief(rowText(it.q))" :subject="null" /></span>
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

    <section ref="sheetEl" class="sheet serif" :class="`lay-${layout}`">
      <div v-if="!basket.count" class="sheet-empty no-print">
        <p>试题篮还是空的，先去挑选题目吧。</p>
        <div class="empty-links">
          <RouterLink to="/chapter" class="btn btn-primary">去选题</RouterLink>
          <RouterLink to="/papers" class="btn btn-outline">从试卷选题整卷组卷</RouterLink>
        </div>
      </div>
      <template v-else>
        <!-- 排版测量用的隐藏副本：按栏宽排出每块的实际高度 -->
        <div class="measure-wrap" aria-hidden="true">
          <div ref="measureEl" class="measure" :style="{ width: `${box.colWidth}mm` }">
            <PaperBlock v-for="b in blocks" :key="b.key" :block="b" :show-ans="opts.showAns" :subtitle="autoSubtitle" />
          </div>
        </div>
        <div class="stage" :style="{ height: `${view.height * scale}px` }">
          <div ref="pagesEl" class="pages" :style="{ width: `${box.w}mm`, transform: `translateX(-50%) scale(${scale})` }">
            <div
              v-for="(page, pi) in pages" :key="pi" class="page" :class="{ binding: opts.binding }"
              :style="{ width: `${box.w}mm`, minHeight: `${box.h}mm`, padding: `${box.margin}mm ${box.margin}mm ${box.margin}mm ${box.left}mm` }"
            >
              <div class="cols" :style="{ gridTemplateColumns: `repeat(${box.cols}, ${box.colWidth}mm)`, columnGap: `${box.colGap}mm` }">
                <div v-for="(col, ci) in page" :key="ci" class="col" :style="{ minHeight: `${box.colHeight}px` }">
                  <PaperBlock
                    v-for="(b, bi) in col" :key="b.key" :block="b" :show-ans="opts.showAns" :subtitle="autoSubtitle" editable
                    :class="{ focus: b.kind === 'item' && focusId === b.key }" :style="{ marginTop: bi ? `${b.gap}px` : 0 }"
                    @pick="focusRow"
                  />
                </div>
              </div>
              <div class="page-foot">{{ subject }}试卷　第 {{ pi + 1 }} 页（共 {{ pages.length }} 页）</div>
            </div>
          </div>
        </div>
      </template>
    </section>

    <aside class="side sticky-side no-print">
      <div class="card settings">
        <span class="card-title">排版设置</span>
        <p class="edit-tip">点击卷头文字（标题、考试时间、注意事项等）可直接修改。<button class="btn-link small" @click="basket.resetHead()">恢复默认卷头</button></p>
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
.sec-move button:hover:not(:disabled), .row-ops button:hover:not(:disabled) { background: var(--c-surface); color: var(--c-primary); }
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
  font-size: 12px; border: 1px solid transparent; background: var(--c-surface); cursor: grab;
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
  flex: 1; border: 1px dashed var(--c-primary); background: var(--c-surface); color: var(--c-primary); border-radius: var(--r-sm);
  height: 34px; font-size: 13px; display: flex; align-items: center; justify-content: center;
}
.more-btn:hover { text-decoration: none; background: var(--c-primary-soft); }

/* 试卷：按纸张实际尺寸分页排版，整体缩放到中间栏宽度 */
.sheet { min-width: 0; flex: 999 1 520px; color: var(--c-ink); }
.sheet-empty {
  min-height: 360px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px;
  font-family: var(--font-sans); color: var(--c-text-3);
  background: var(--c-surface); border: 1px solid var(--c-border); border-radius: 4px;
}
.sheet-empty p { margin: 0; font-size: 15px; }
.empty-links { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }
.empty-links .btn { padding: 0 18px; display: inline-flex; align-items: center; }
.empty-links .btn:hover { text-decoration: none; }
.measure-wrap { height: 0; overflow: hidden; visibility: hidden; pointer-events: none; }
.measure { display: flow-root; }
.stage { position: relative; }
.pages { position: absolute; top: 0; left: 50%; transform-origin: top center; display: flex; flex-direction: column; gap: 24px; }
.page {
  position: relative; box-sizing: border-box; background: var(--c-surface); border: 1px solid var(--c-border); border-radius: 2px;
  box-shadow: 0 10px 30px rgb(var(--shadow-rgb) / .08);
}
.page.binding::before {
  content: '装　订　线'; position: absolute; left: 12mm; top: 20mm; bottom: 20mm; width: 6mm;
  border-right: 1px dashed var(--c-border-strong); writing-mode: vertical-rl; display: flex; align-items: center; justify-content: center;
  font-size: 11px; letter-spacing: 8px; color: var(--c-text-4); font-family: var(--font-sans);
}
.cols { display: grid; align-items: stretch; }
.col { display: flow-root; min-width: 0; }
.col + .col { position: relative; }
.col + .col::before { content: ''; position: absolute; top: 0; bottom: 0; left: -7.5mm; border-left: 1px dashed var(--c-border); }
.page-foot { position: absolute; left: 0; right: 0; bottom: 9mm; text-align: center; font-size: 12px; color: var(--c-text-3); font-family: var(--font-sans); }
.focus { background: var(--c-primary-soft); }

/* 右侧设置 */
.side { flex: 1 0 240px; display: flex; flex-direction: column; gap: 14px; }
.side .btn:disabled { opacity: .5; cursor: default; }
.export-err { margin: -6px 0 0; font-size: 12px; color: var(--c-hard); }
.settings { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
.edit-tip { margin: 0; font-size: 12px; line-height: 1.6; color: var(--c-text-4); }
.edit-tip .btn-link { margin-left: 4px; }
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
}

@media print {
  .paper { padding: 0; display: block; max-width: none; }
  .stage { height: auto !important; }
  .pages { position: static; transform: none !important; gap: 0; }
  .page { border: none; border-radius: 0; box-shadow: none; break-after: page; }
  .page:last-child { break-after: auto; }
  .focus { background: none; }
}
</style>

<style>
@media print {
  .no-print, header, footer { display: none !important; }
  body { background: #fff; }
}
</style>
