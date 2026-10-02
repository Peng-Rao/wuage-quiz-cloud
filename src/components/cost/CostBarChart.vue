<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

export interface ChartSeries { name: string; color: string }
export interface ChartBar {
  /** 提示框标题 */
  label: string
  /** 横轴刻度文字 */
  tick: string
  /** 与 series 一一对应，自下而上堆叠 */
  values: number[]
  /** 背景加浅色底（如高峰时段） */
  shaded?: boolean
  /** 提示框中追加的说明行 */
  detail?: string[]
}

const props = withDefaults(defineProps<{
  bars: ChartBar[]
  series: ChartSeries[]
  /** 提示框中的金额格式 */
  format: (v: number) => string
  /** 纵轴刻度格式，默认同 format */
  axisFormat?: (v: number) => string
  height?: number
  /** 图的无障碍说明 */
  title: string
}>(), { height: 220 })

const box = ref<HTMLElement>()
const width = ref(640)
let observer: ResizeObserver | null = null
onMounted(() => {
  observer = new ResizeObserver(([e]) => { width.value = Math.max(240, Math.round(e.contentRect.width)) })
  if (box.value) observer.observe(box.value)
})
onBeforeUnmount(() => observer?.disconnect())

const PAD = { top: 8, right: 4, bottom: 24, left: 56 }
const GAP = 2

const totals = computed(() => props.bars.map(b => b.values.reduce((a, v) => a + v, 0)))

/** 取整的刻度：最大值向上取到 1 / 2 / 2.5 / 5 × 10^n */
const ticks = computed(() => {
  const max = Math.max(...totals.value, 0)
  if (max <= 0) return [0]
  const raw = max / 4
  const pow = 10 ** Math.floor(Math.log10(raw))
  const step = ([1, 2, 2.5, 5, 10].find(m => m * pow >= raw) ?? 10) * pow
  return Array.from({ length: Math.ceil(max / step) + 1 }, (_, i) => +(i * step).toPrecision(12))
})
const yMax = computed(() => ticks.value[ticks.value.length - 1] || 1)
const plotW = computed(() => width.value - PAD.left - PAD.right)
const plotH = computed(() => props.height - PAD.top - PAD.bottom)
const slot = computed(() => plotW.value / Math.max(props.bars.length, 1))
const barW = computed(() => Math.max(2, Math.min(28, slot.value * 0.66)))
const y = (v: number) => PAD.top + plotH.value - (v / yMax.value) * plotH.value
const xCenter = (i: number) => PAD.left + slot.value * (i + 0.5)
/** 横轴刻度间隔：保证文字不重叠 */
const tickEvery = computed(() => Math.max(1, Math.ceil(props.bars.length / Math.max(1, Math.floor(plotW.value / 52)))))

/** 顶端圆角、底端贴基线的矩形 */
function barPath(x: number, top: number, bottom: number, w: number, rounded: boolean): string {
  const h = bottom - top
  if (h <= 0) return ''
  const r = rounded ? Math.min(4, w / 2, h) : 0
  return `M${x},${bottom}V${top + r}${r ? `Q${x},${top} ${x + r},${top}` : ''}H${x + w - r}` +
    `${r ? `Q${x + w},${top} ${x + w},${top + r}` : ''}V${bottom}Z`
}

const segments = computed(() => props.bars.map((b, i) => {
  const x = xCenter(i) - barW.value / 2
  const out: { d: string; color: string }[] = []
  let acc = 0
  const last = b.values.reduce((k, v, j) => (v > 0 ? j : k), -1)
  b.values.forEach((v, j) => {
    if (v <= 0) return
    const bottom = y(acc) - (acc > 0 ? GAP : 0)
    acc += v
    out.push({ d: barPath(x, y(acc), bottom, barW.value, j === last), color: props.series[j].color })
  })
  return out
}))

const hover = ref<number | null>(null)
const tip = computed(() => {
  if (hover.value == null) return null
  const i = hover.value
  const b = props.bars[i]
  // 放在柱子旁边，不遮挡当前柱；右半边的柱放到左侧
  const right = xCenter(i) > width.value / 2
  const left = xCenter(i) + (right ? -1 : 1) * (barW.value / 2 + 8)
  return { b, left, right, total: totals.value[i] }
})
</script>

<template>
  <div ref="box" class="chart" @mouseleave="hover = null">
    <svg :width="width" :height="height" role="img" :aria-label="title">
      <g class="grid">
        <template v-for="t in ticks" :key="t">
          <line :x1="PAD.left" :x2="width - PAD.right" :y1="y(t)" :y2="y(t)" :class="{ base: t === 0 }" />
          <text :x="PAD.left - 8" :y="y(t)" dy="0.32em" text-anchor="end">{{ (axisFormat ?? format)(t) }}</text>
        </template>
      </g>
      <g>
        <rect
          v-for="(b, i) in bars" v-show="b.shaded" :key="'s' + i" class="shade"
          :x="PAD.left + slot * i" :y="PAD.top" :width="slot" :height="plotH"
        />
      </g>
      <g v-for="(segs, i) in segments" :key="i" :class="{ dim: hover != null && hover !== i }" class="bar">
        <path v-for="(s, j) in segs" :key="j" :d="s.d" :fill="s.color" />
      </g>
      <g class="ticks">
        <template v-for="(b, i) in bars" :key="'t' + i">
          <text v-if="i % tickEvery === 0" :x="xCenter(i)" :y="height - 6" text-anchor="middle">{{ b.tick }}</text>
        </template>
      </g>
      <rect
        v-for="(_, i) in bars" :key="'h' + i" class="hit"
        :x="PAD.left + slot * i" :y="PAD.top" :width="slot" :height="plotH"
        @mouseenter="hover = i"
      />
    </svg>
    <div v-if="tip" class="tip" :class="{ flip: tip.right }" :style="{ left: tip.left + 'px' }">
      <b>{{ tip.b.label }}</b>
      <span class="tip-total">{{ format(tip.total) }}</span>
      <template v-if="series.length > 1">
        <span v-for="(s, j) in series" :key="s.name" class="tip-row">
          <i :style="{ background: s.color }" />{{ s.name }}<em>{{ format(tip.b.values[j]) }}</em>
        </span>
      </template>
      <span v-for="line in tip.b.detail" :key="line" class="tip-note">{{ line }}</span>
    </div>
  </div>
</template>

<style scoped>
.chart { position: relative; width: 100%; min-width: 0; }
svg { display: block; overflow: visible; }
text { font-size: 11px; fill: var(--c-text-4); font-variant-numeric: tabular-nums; }
.grid line { stroke: var(--c-divider); stroke-width: 1; }
.grid line.base { stroke: var(--c-border); }
.shade { fill: var(--c-primary-soft); opacity: .55; pointer-events: none; }
.bar path { transition: opacity .12s; }
.bar.dim path { opacity: .45; }
.hit { fill: transparent; cursor: default; }
.tip {
  position: absolute; top: 0; pointer-events: none; z-index: 2;
  min-width: 150px; padding: 8px 10px; background: #fff; border: 1px solid var(--c-border);
  border-radius: var(--r-md); box-shadow: 0 6px 20px rgba(27, 36, 48, .12);
  display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--c-text-2);
  font-variant-numeric: tabular-nums;
}
.tip.flip { transform: translateX(-100%); }
.tip b { font-weight: 600; color: var(--c-ink); }
.tip-total { font-size: 15px; font-weight: 700; color: var(--c-ink); }
.tip-row { display: flex; align-items: center; gap: 6px; }
.tip-row i { width: 8px; height: 8px; border-radius: 2px; flex-shrink: 0; }
.tip-row em { margin-left: auto; font-style: normal; padding-left: 12px; }
.tip-note { color: var(--c-text-3); }
</style>
