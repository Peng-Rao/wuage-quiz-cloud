<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { BasketQuestion } from '@/stores/basket'
import { useBasketStore } from '@/stores/basket'
import MathText from '@/components/MathText.vue'
import EditableText from '@/components/EditableText.vue'
import { noSep, optionCols, optionLabel, useTextLayout } from '@/utils/subject'

/** 试卷中的一块：卷头、注意事项、大题标题、阅读材料、题目、答题卡。分页时整块放入一栏 */
export type PaperBlockData = { key: string; gap: number; keepWithNext?: boolean } & (
  | { kind: 'head' | 'notice' | 'card' }
  | { kind: 'part'; heading: string }
  | { kind: 'material'; text: string }
  | { kind: 'item'; no: number; scoreMark: string; q: BasketQuestion }
)

const props = defineProps<{
  block: PaperBlockData
  /** 卷头可直接修改；排版测量用的副本为 false */
  editable?: boolean
  showAns: boolean
  subtitle: string
}>()
defineEmits<{ pick: [id: string] }>()

const basket = useBasketStore()
const head = computed(() => basket.head)
const layout = useTextLayout()

/**
 * 选项每行排 4 / 2 / 1 个：按各选项在卷面上的实际宽度决定，放得下就并排。
 * 不可见的探针按不换行排出每个选项，量出最宽的一个。
 */
const optEl = ref<HTMLElement | null>(null)
const probeEl = ref<HTMLElement | null>(null)
const cols = ref<1 | 2 | 4>(props.block.kind === 'item' ? optionCols(props.block.q.options) : 1)
function fitOptions() {
  const box = optEl.value, probe = probeEl.value
  if (!box || !probe || !box.clientWidth) return
  const style = getComputedStyle(box)
  const avail = box.clientWidth - parseFloat(style.paddingLeft)
  const gap = parseFloat(style.columnGap) || 0
  // 卷面整体缩放显示，量到的宽度换算回排版尺寸
  const zoom = box.getBoundingClientRect().width / box.offsetWidth || 1
  const widest = Math.max(...[...probe.children].map((c) => c.getBoundingClientRect().width)) / zoom
  cols.value = widest <= (avail - gap * 3) / 4 ? 4 : widest <= (avail - gap) / 2 ? 2 : 1
}
const optObs = new ResizeObserver(fitOptions)
onMounted(() => {
  for (const el of [optEl.value, probeEl.value]) if (el) optObs.observe(el)
})
onBeforeUnmount(() => optObs.disconnect())
</script>

<template>
  <div v-if="block.kind === 'head'" class="head center">
    <div class="secret"><EditableText v-model="head.secret" placeholder="（保密级别，可留空）" :readonly="!editable" /></div>
    <h1><EditableText v-model="head.title" placeholder="（试卷标题）" :readonly="!editable" /></h1>
    <div class="subject">
      <EditableText :model-value="head.subtitle ?? subtitle" placeholder="（科目）" :readonly="!editable" @update:model-value="head.subtitle = $event" />
    </div>
    <div class="info"><EditableText v-model="head.duration" placeholder="（考试时间）" :readonly="!editable" />　满分：{{ basket.totalScore }} 分</div>
    <div class="fields">
      <span v-for="(_, i) in head.fields" :key="i"><EditableText v-model="head.fields[i]" placeholder="（栏目）" :readonly="!editable" />：__________</span>
    </div>
  </div>

  <div v-else-if="block.kind === 'notice'" class="notice">
    <EditableText v-model="head.notice" placeholder="（注意事项，可留空）" multiline :readonly="!editable" />
  </div>

  <div v-else-if="block.kind === 'part'" class="part-head">{{ block.heading }}</div>

  <div v-else-if="block.kind === 'material'" class="material"><MathText :text="block.text" block /></div>

  <div v-else-if="block.kind === 'item'" class="item" @click="$emit('pick', block.q.id)">
    <div class="stem">
      {{ block.no }}{{ noSep(layout) }}{{ block.scoreMark }}<MathText :text="block.q.stem" />
    </div>
    <div v-if="block.q.images.length" class="images">
      <img v-for="src in block.q.images" :key="src" :src="src" alt="题目配图">
    </div>
    <div v-if="block.q.options.length" ref="optEl" class="options opt-fixed" :class="`cols-${cols}`">
      <span v-for="(o, i) in block.q.options" :key="i">{{ optionLabel(layout, i) }}<MathText :text="o" /></span>
      <div ref="probeEl" class="opt-probe" aria-hidden="true">
        <span v-for="(o, i) in block.q.options" :key="i">{{ optionLabel(layout, i) }}<MathText :text="o" /></span>
      </div>
    </div>
    <div v-if="showAns" class="answer">
      【答案】<MathText :text="block.q.answer || '略'" /><template v-if="block.q.analysis">　【解析】<MathText :text="block.q.analysis" /></template>
    </div>
  </div>

  <div v-else :id="editable ? 'answer-card' : undefined" class="answer-card">
    <div class="part-head">答题卡</div>
    <div class="card-grid">
      <div v-for="n in basket.count" :key="n" class="card-cell">
        <span>{{ n }}</span><span class="blank" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.center { text-align: center; }
.head { display: flex; flex-direction: column; gap: 10px; }
.secret { font-size: 12px; letter-spacing: 4px; color: var(--c-text-3); }
.head h1 { margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 1px; line-height: 1.4; }
.subject { font-size: 20px; font-weight: 600; letter-spacing: 6px; }
.info { font-size: 13px; color: var(--c-text-2); }
.fields { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px 28px; font-size: 13px; color: var(--c-text-2); padding-top: 4px; }
.notice { border: 1px solid var(--c-border-strong); padding: 10px 14px; font-size: 13px; line-height: 1.8; color: var(--c-text-2); }
.notice .editable { display: block; }

.part-head { font-size: 15.5px; font-weight: 700; font-family: var(--font-sans); line-height: 1.6; }
.item { display: flex; flex-direction: column; gap: 6px; font-size: 15px; line-height: 1.9; border-radius: 4px; cursor: pointer; }
.item:hover { background: var(--c-surface-2); }
.stem { text-wrap: pretty; }
.material { font-size: 15px; line-height: 1.9; }
.images { display: flex; flex-wrap: wrap; gap: 10px; padding-left: 1.5em; }
.images img { max-width: min(100%, 320px); max-height: 220px; object-fit: contain; }
.options { position: relative; display: grid; gap: 0 20px; padding-left: 1.5em; }
.opt-probe { position: absolute; left: 0; top: 0; width: 0; height: 0; overflow: hidden; visibility: hidden; pointer-events: none; }
.opt-probe > span { display: block; width: max-content; white-space: nowrap; }
.opt-probe :deep(.math-text) { white-space: nowrap; }
/* 纸张宽度固定，选项分列不随窗口宽度变化（盖过 base.css 中的窄屏规则） */
.options.opt-fixed.opt-fixed.cols-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.options.opt-fixed.opt-fixed.cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.answer { font-size: 13px; color: var(--c-primary); font-family: var(--font-sans); padding-left: 1.5em; line-height: 1.7; }

.answer-card { border-top: 2px solid var(--c-ink); padding-top: 16px; display: flex; flex-direction: column; gap: 12px; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.card-cell { display: flex; align-items: center; gap: 8px; font-size: 13px; font-family: var(--font-sans); }
.card-cell .blank { flex: 1; height: 22px; border: 1px solid var(--c-border-strong); border-radius: 3px; }

@media print {
  .item:hover { background: none; }
}
</style>
