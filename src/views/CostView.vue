<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { usageApi, type CostAnalysis, type CostBreakdown, type ModelPrice } from '@/api/usage'
import LoadingState from '@/components/LoadingState.vue'
import CostBarChart, { type ChartBar } from '@/components/cost/CostBarChart.vue'
import BreakdownList, { type BreakdownItem } from '@/components/cost/BreakdownList.vue'
import {
  PURPOSE_LABELS, SOURCE_LABELS, formatCost, formatDuration, formatPercent, formatTokens, pricingNote,
} from '@/utils/usage'

// ---- 日期区间（北京时间）----
const BJ = 8 * 3600_000
const iso = (t: number) => new Date(t + BJ).toISOString().slice(0, 10)
const todayIso = () => iso(Date.now())
const shift = (d: string, days: number) => iso(Date.parse(d + 'T00:00:00Z') - BJ + days * 86400_000)

type Preset = '7' | '30' | '90' | 'month' | 'last-month'
const PRESETS: { key: Preset; label: string }[] = [
  { key: '7', label: '近 7 天' },
  { key: '30', label: '近 30 天' },
  { key: '90', label: '近 90 天' },
  { key: 'month', label: '本月' },
  { key: 'last-month', label: '上月' },
]
function presetRange(p: Preset): [string, string] {
  const today = todayIso()
  if (p === 'month') return [today.slice(0, 8) + '01', today]
  if (p === 'last-month') {
    const end = shift(today.slice(0, 8) + '01', -1)
    return [end.slice(0, 8) + '01', end]
  }
  return [shift(today, -(Number(p) - 1)), today]
}

const preset = ref<Preset | null>('30')
const start = ref(presetRange('30')[0])
const end = ref(presetRange('30')[1])

const data = ref<CostAnalysis | null>(null)
const prices = ref<ModelPrice[]>([])
const loading = ref(false)
const error = ref('')
let seq = 0

async function load() {
  if (start.value > end.value) { error.value = '开始日期不能晚于结束日期'; return }
  const id = ++seq
  loading.value = true
  error.value = ''
  try {
    const result = await usageApi.analysis(start.value, end.value)
    if (id === seq) data.value = result
  } catch (e) {
    if (id === seq) error.value = (e as Error).message
  } finally {
    if (id === seq) loading.value = false
  }
}
function pick(p: Preset) {
  preset.value = p
  ;[start.value, end.value] = presetRange(p)
  load()
}
function custom() {
  preset.value = null
  if (start.value && end.value) load()
}
onMounted(() => {
  load()
  usageApi.prices().then(p => (prices.value = p)).catch(() => {})
})

// ---- 展示 ----
const d = computed(() => data.value)
const cur = computed(() => d.value?.summary.currency ?? '¥')
const money = (v: number | null | undefined) => formatCost(v, cur.value)
/** 坐标轴刻度：不带多余小数 */
const axisMoney = (v: number) => cur.value + (v >= 100 ? v.toFixed(0) : v >= 1 ? +v.toFixed(2) : +v.toPrecision(2))
const shortDate = (s: string) => s.slice(5).replace('-', '/')

const delta = computed(() => {
  const o = d.value
  if (!o || o.summary.cost == null || o.previousCost == null) return null
  if (o.previousCost === 0) return o.summary.cost === 0 ? 0 : null
  return (o.summary.cost - o.previousCost) / o.previousCost
})
const note = computed(() => (d.value?.summary.calls ? pricingNote(d.value.summary) : ''))
const otherCurrencies = computed(() => {
  const o = d.value
  if (!o) return ''
  const main = Object.entries(o.summary.costsByCurrency).sort((a, b) => b[1] - a[1])[0]?.[0]
  return Object.entries(o.summary.costsByCurrency).filter(([k]) => k !== main)
    .map(([k, v]) => `${k} ${v.toFixed(4)}`).join('、')
})

const SERIES = [
  { name: '大模型', color: '#B8561F' },
  { name: 'MinerU', color: '#2A78D6' },
]
const showMineru = computed(() => !!d.value?.daily.some(x => x.mineruCost > 0))
const dailyBars = computed<ChartBar[]>(() => (d.value?.daily ?? []).map(x => ({
  label: x.date,
  tick: shortDate(x.date),
  values: showMineru.value ? [x.llmCost, x.mineruCost] : [x.llmCost],
  detail: [`${x.calls} 次调用 · ${x.jobs} 份试卷`, `${formatTokens(x.totalTokens)} tokens${x.pages ? ` · ${x.pages} 页` : ''}`],
})))
const hourlyBars = computed<ChartBar[]>(() => (d.value?.hourly ?? []).map(h => ({
  label: `${h.hour}:00–${h.hour + 1}:00${h.peak ? '（工作日高峰）' : ''}`,
  tick: String(h.hour),
  values: [h.cost],
  shaded: h.peak,
  detail: [`${h.calls} 次调用`],
})))
const hasPeak = computed(() => !!d.value?.hourly.some(h => h.peak))
const showTable = ref(false)

function tokensSub(b: CostBreakdown): string {
  const parts = [`${b.calls} 次`]
  if (b.provider === 'mineru' || (b.pages && !b.promptTokens)) parts.push(`${b.pages} 页`)
  else parts.push(`${formatTokens(b.promptTokens + b.completionTokens)} tokens`)
  if (b.unpricedCalls) parts.push(`${b.unpricedCalls} 次未计价`)
  return parts.join(' · ')
}
const purposeItems = computed<BreakdownItem[]>(() => (d.value?.byPurpose ?? []).map(b => ({
  key: b.key, label: PURPOSE_LABELS[b.key] ?? b.key, sub: tokensSub(b), value: b.cost,
})))
const userItems = computed<BreakdownItem[]>(() => (d.value?.byUser ?? []).map(b => ({
  key: b.key || '-', label: b.label, sub: `${b.jobs} 份试卷 · ${tokensSub(b)}`, value: b.cost,
})))
const subjectItems = computed<BreakdownItem[]>(() => (d.value?.bySubject ?? []).map(b => ({
  key: b.key || '-', label: b.label, sub: `${b.jobs} 份试卷 · ${tokensSub(b)}`, value: b.cost,
})))

const STATUS: Record<string, string> = { done: '已完成', failed: '失败', running: '解析中', queued: '排队中', canceled: '已取消' }
const fmtTime = (s: string) => new Date(s).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })

const billingPrices = computed(() => prices.value.filter(p => p.billing))
function priceText(p: ModelPrice): string {
  const t = p.tiers[0] ?? {}
  if (t.per_page != null) return `${t.per_page} / 页`
  const off = t.input_offpeak != null ? `（闲时 ${t.input_offpeak} / ${t.output_offpeak}）` : ''
  return `${t.input} / ${t.output}${off}`
}
function cachedText(p: ModelPrice): string {
  const t = p.tiers[0] ?? {}
  if (t.per_page != null) return '—'
  if (t.cached_input != null) return String(t.cached_input)
  return p.cacheRatio != null && t.input != null ? `${+(t.input * p.cacheRatio).toPrecision(4)}（${formatPercent(p.cacheRatio, 0)}）` : '—'
}
</script>

<template>
  <main class="container cost-page">
    <div class="page-heading">
      <div>
        <h1>AI 成本分析</h1>
        <p>大模型与 MinerU 的调用费用：按调用时的单价计算，仅供估算，以厂商账单为准。</p>
      </div>
      <span class="tag">仅管理员</span>
    </div>

    <div class="toolbar">
      <div class="presets" role="group" aria-label="时间范围">
        <button
          v-for="p in PRESETS" :key="p.key" class="chip" :class="{ 'is-solid': preset === p.key }"
          :aria-pressed="preset === p.key" @click="pick(p.key)"
        >{{ p.label }}</button>
      </div>
      <div class="dates">
        <input v-model="start" type="date" aria-label="开始日期" :max="end" @change="custom">
        <span>至</span>
        <input v-model="end" type="date" aria-label="结束日期" :min="start" @change="custom">
        <button class="btn" :disabled="loading" @click="load">刷新</button>
      </div>
    </div>

    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <LoadingState v-if="loading && !d" label="正在统计成本…" />

    <template v-if="d">
      <div class="notes" :class="{ stale: loading }">
        <p v-if="note" class="warn">{{ note }}</p>
        <p v-if="otherCurrencies">另有其他货币的费用未计入合计：{{ otherCurrencies }}</p>
        <p v-if="d.summary.estimated">部分调用服务端未返回用量，已按字符数估算。</p>
      </div>

      <section class="kpis" :class="{ stale: loading }">
        <div class="card kpi hero">
          <span class="kpi-label">区间总费用<small>{{ d.start }} 至 {{ d.end }}</small></span>
          <span class="kpi-num">{{ money(d.summary.cost) }}</span>
          <span class="kpi-sub">
            <template v-if="delta != null">较上期 <b :class="delta > 0 ? 'up' : 'down'">{{ delta > 0 ? '↑' : delta < 0 ? '↓' : '' }}{{ formatPercent(Math.abs(delta)) }}</b>（{{ money(d.previousCost) }}）</template>
            <template v-else>上期（{{ shortDate(d.previousStart) }}–{{ shortDate(d.previousEnd) }}）无可比费用</template>
          </span>
          <span class="kpi-sub">大模型 {{ money(d.summary.llmCost ?? 0) }} · MinerU {{ money(d.summary.mineruCost ?? 0) }}</span>
        </div>
        <div class="card kpi">
          <span class="kpi-label">日均费用</span>
          <span class="kpi-num">{{ money(d.avgDailyCost) }}</span>
          <span class="kpi-sub">按此估算每月约 <b>{{ money(d.projectedMonthlyCost) }}</b></span>
        </div>
        <div class="card kpi">
          <span class="kpi-label">每份试卷</span>
          <span class="kpi-num">{{ money(d.costPerJob) }}</span>
          <span class="kpi-sub">{{ d.jobs }} 份 · {{ d.pages }} 页 · 每页 {{ money(d.costPerPage) }}</span>
        </div>
        <div class="card kpi">
          <span class="kpi-label">每道题</span>
          <span class="kpi-num">{{ money(d.costPerQuestion) }}</span>
          <span class="kpi-sub">解析出 {{ d.questions }} 题</span>
        </div>
        <div class="card kpi">
          <span class="kpi-label">调用</span>
          <span class="kpi-num">{{ d.summary.calls.toLocaleString('zh-CN') }}<small> 次</small></span>
          <span class="kpi-sub">{{ formatTokens(d.summary.totalTokens) }} tokens · {{ formatDuration(d.summary.durationMs) }}</span>
          <span v-if="d.summary.errors" class="kpi-sub">失败 {{ d.summary.errors }} 次，费用 {{ money(d.errorCost) }}</span>
        </div>
      </section>

      <section class="card block" :class="{ stale: loading }">
        <div class="block-head">
          <h2>每日费用</h2>
          <div class="legend-row">
            <template v-if="showMineru">
              <span v-for="s in SERIES" :key="s.name" class="key"><i :style="{ background: s.color }" />{{ s.name }}</span>
            </template>
            <button class="btn-link is-primary" @click="showTable = !showTable">{{ showTable ? '查看图表' : '查看表格' }}</button>
          </div>
        </div>
        <CostBarChart
          v-if="!showTable" title="每日费用柱状图" :bars="dailyBars"
          :series="showMineru ? SERIES : SERIES.slice(0, 1)" :format="money" :axis-format="axisMoney"
        />
        <div v-else class="table-wrap">
          <table class="data">
            <thead><tr><th>日期</th><th>调用</th><th>试卷</th><th>tokens</th><th>MinerU 页数</th><th>大模型</th><th>MinerU</th><th>合计</th></tr></thead>
            <tbody>
              <tr v-for="x in [...d.daily].reverse()" :key="x.date">
                <td>{{ x.date }}</td><td>{{ x.calls }}</td><td>{{ x.jobs }}</td><td>{{ formatTokens(x.totalTokens) }}</td>
                <td>{{ x.pages }}</td><td>{{ money(x.llmCost) }}</td><td>{{ money(x.mineruCost) }}</td><td><b>{{ money(x.cost) }}</b></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div class="grid-2" :class="{ stale: loading }">
        <section class="card block">
          <div class="block-head"><h2>按用途</h2><small>解析流程各环节与组卷、查重</small></div>
          <BreakdownList :items="purposeItems" :format="money" empty="区间内没有调用" />
        </section>
        <section class="card block">
          <div class="block-head"><h2>按学科</h2><small>解析任务按试卷学科归类</small></div>
          <BreakdownList :items="subjectItems" :format="money" empty="区间内没有调用" />
        </section>
      </div>

      <section class="card block" :class="{ stale: loading }">
        <div class="block-head"><h2>按模型</h2><small>计费来源：{{ SOURCE_LABELS[d.billingSource] ?? d.billingSource }}</small></div>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>模型</th><th>调用</th><th>输入 tokens</th><th>输出 tokens</th><th>缓存命中</th><th>失败</th><th>每次均价</th><th>费用</th><th>占比</th></tr></thead>
            <tbody>
              <tr v-for="m in d.byModel" :key="m.key">
                <td class="model">{{ m.key }}<small v-if="m.unpricedCalls">{{ m.unpricedCalls }} 次未计价</small></td>
                <td>{{ m.calls }}</td>
                <template v-if="m.provider === 'mineru'"><td>{{ m.pages }} 页</td><td>—</td><td>—</td></template>
                <template v-else>
                  <td>{{ formatTokens(m.promptTokens) }}</td>
                  <td>{{ formatTokens(m.completionTokens) }}</td>
                  <td>{{ m.promptTokens ? formatPercent(m.cachedTokens / m.promptTokens) : '—' }}</td>
                </template>
                <td>{{ m.errors || '—' }}</td>
                <td>{{ m.calls > m.unpricedCalls ? money(m.cost / (m.calls - m.unpricedCalls)) : '—' }}</td>
                <td><b>{{ money(m.cost) }}</b></td>
                <td>{{ d.summary.cost ? formatPercent(m.cost / d.summary.cost) : '—' }}</td>
              </tr>
              <tr v-if="!d.byModel.length"><td colspan="9" class="muted">区间内没有调用</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <div class="grid-2" :class="{ stale: loading }">
        <section class="card block">
          <div class="block-head"><h2>按账号</h2><small>解析任务计入上传者</small></div>
          <BreakdownList :items="userItems" :format="money" empty="区间内没有调用" />
        </section>
        <section class="card block">
          <div class="block-head">
            <h2>调用时段</h2>
            <span v-if="hasPeak" class="key"><i class="peak-key" />工作日高峰价时段</span>
          </div>
          <CostBarChart title="各时段费用柱状图" :bars="hourlyBars" :series="SERIES.slice(0, 1)" :format="money" :axis-format="axisMoney" :height="180" />
          <ul class="insights">
            <li v-if="d.peakCostShare != null">高峰时段的大模型费用占 <b>{{ formatPercent(d.peakCostShare) }}</b>；批量解析可安排在空闲时段（北京时间），以闲时价计费。</li>
            <li v-if="d.cacheHitRate != null">输入 tokens 缓存命中率 <b>{{ formatPercent(d.cacheHitRate) }}</b>，命中部分按缓存单价计费。</li>
            <li v-if="d.errorCost > 0">失败调用产生费用 <b>{{ money(d.errorCost) }}</b>，失败请求多数仍按输入计费。</li>
          </ul>
        </section>
      </div>

      <section class="card block" :class="{ stale: loading }">
        <div class="block-head"><h2>费用最高的试卷</h2><small>区间内的调用费用，含 AI 解答与相似题检索</small></div>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>试卷</th><th>上传者</th><th>学科</th><th>页数</th><th>题数</th><th>调用</th><th>tokens</th><th>每题</th><th>费用</th></tr></thead>
            <tbody>
              <tr v-for="j in d.topJobs" :key="j.id">
                <td class="file">
                  <RouterLink :to="{ path: '/upload', query: { job: j.id } }">{{ j.fileName }}</RouterLink>
                  <small>{{ fmtTime(j.createdAt) }} · {{ STATUS[j.status] ?? j.status }}</small>
                </td>
                <td>{{ j.owner ?? '—' }}</td>
                <td>{{ j.subject ?? '—' }}</td>
                <td>{{ j.pages ?? '—' }}</td>
                <td>{{ j.questions }}</td>
                <td>{{ j.calls }}</td>
                <td>{{ formatTokens(j.totalTokens) }}</td>
                <td>{{ j.questions ? money(j.cost / j.questions) : '—' }}</td>
                <td><b>{{ money(j.cost) }}</b></td>
              </tr>
              <tr v-if="!d.topJobs.length"><td colspan="9" class="muted">区间内没有解析任务</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="billingPrices.length" class="card block">
        <div class="block-head"><h2>计费单价</h2><small>每百万 tokens；每天自动从厂商价格页更新，价格变化后的调用按新价计算</small></div>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>模型</th><th>来源</th><th>输入 / 输出</th><th>缓存命中</th><th>阶梯</th><th>生效</th><th>核对</th></tr></thead>
            <tbody>
              <tr v-for="p in billingPrices" :key="p.id">
                <td class="model">{{ p.model }}</td>
                <td>{{ SOURCE_LABELS[p.source] ?? p.source }}</td>
                <td>{{ p.currency }} {{ priceText(p) }}</td>
                <td>{{ cachedText(p) }}</td>
                <td>{{ p.tiers.length > 1 ? `${p.tiers.length} 档` : '—' }}</td>
                <td>{{ p.fetchedAt.slice(0, 10) }}</td>
                <td>{{ fmtTime(p.checkedAt) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.cost-page { padding-top: 32px; padding-bottom: 48px; display: flex; flex-direction: column; gap: 20px; }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h1 { font-size: 26px; margin: 0 0 10px; }
.page-heading p { color: var(--c-text-3); font-size: 14px; margin: 0; }
h2 { font-size: 15px; margin: 0; font-weight: 600; }

.toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; }
.presets { display: flex; flex-wrap: wrap; gap: 4px; background: #fff; border: 1px solid var(--c-border); border-radius: var(--r-md); padding: 3px; }
.presets .chip { padding: 5px 12px; }
.dates { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--c-text-3); }
.dates input { height: 36px; padding: 0 8px; border: 1px solid var(--c-border); border-radius: var(--r-sm); background: #fff; font: inherit; color: var(--c-ink); }
.dates .btn { height: 36px; }
.error { color: #a0301f; margin: 0; }

.notes:empty { display: none; }
.notes p { margin: 0; font-size: 13px; color: var(--c-text-3); line-height: 1.7; }
.notes .warn { color: #8a4a12; }
.stale { opacity: .6; transition: opacity .15s; }

.kpis { display: grid; grid-template-columns: 1.4fr repeat(4, minmax(0, 1fr)); gap: 12px; }
.kpi { padding: 16px 18px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.kpi-label { font-size: 13px; color: var(--c-text-3); display: flex; flex-wrap: wrap; gap: 0 8px; align-items: baseline; }
.kpi-label small { font-size: 11px; color: var(--c-text-4); }
.kpi-num { font-size: 24px; font-weight: 700; line-height: 1.15; font-variant-numeric: tabular-nums; color: var(--c-ink); }
.kpi-num small { font-size: 13px; font-weight: 400; color: var(--c-text-3); }
.hero .kpi-num { font-size: 32px; color: var(--c-primary); }
.kpi-sub { font-size: 12px; color: var(--c-text-3); line-height: 1.5; font-variant-numeric: tabular-nums; }
.kpi-sub b { font-weight: 600; color: var(--c-ink); }

.block { padding: 18px 20px; display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.block-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px 16px; flex-wrap: wrap; }
.block-head small { font-size: 12px; color: var(--c-text-4); }
.legend-row { display: flex; align-items: center; gap: 14px; }
.key { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-text-3); }
.key i { width: 10px; height: 10px; border-radius: 2px; }
.key .peak-key { background: var(--c-primary-soft); border: 1px solid var(--c-primary-line); }
.grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; align-items: start; }

.table-wrap { overflow-x: auto; margin: 0 -4px; }
.data { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
.data th { text-align: right; font-weight: 400; font-size: 12px; color: var(--c-text-4); padding: 6px 8px; border-bottom: 1px solid var(--c-border); white-space: nowrap; }
.data td { text-align: right; padding: 9px 8px; border-bottom: 1px solid var(--c-divider); white-space: nowrap; color: var(--c-text-2); }
.data th:first-child, .data td:first-child { text-align: left; }
.data td b { color: var(--c-ink); font-weight: 600; }
.data td.muted { text-align: left; color: var(--c-text-4); }
.data .model, .data .file { white-space: normal; min-width: 160px; }
.data .model small, .data .file small { display: block; font-size: 11px; color: var(--c-text-4); margin-top: 2px; }
.data .file a { color: var(--c-ink); }
.data .file a:hover { color: var(--c-primary); }

.insights { margin: 0; padding: 0 0 0 18px; display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; color: var(--c-text-3); line-height: 1.6; }
.insights:empty { display: none; }
.insights b { color: var(--c-ink); }

@media (max-width: 1100px) {
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .hero { grid-column: 1 / -1; }
}
@media (max-width: 800px) {
  .grid-2 { grid-template-columns: 1fr; }
  .toolbar { flex-direction: column; align-items: stretch; }
  .dates { flex-wrap: wrap; }
}
@media (max-width: 480px) {
  .kpis { grid-template-columns: 1fr; }
  .cost-page { padding-top: 20px; }
}
</style>
