<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { parseApi, type DifficultyCalibration, type EvalMetrics, type EvalRun, type EvalSample } from '@/api/parse'

const samples = ref<EvalSample[]>([])
const runs = ref<EvalRun[]>([])
const current = ref<EvalRun | null>(null)
const calibration = ref<DifficultyCalibration | null>(null)
const error = ref('')
const notice = ref('')
let timer: ReturnType<typeof setTimeout> | null = null
let disposed = false

async function load() {
  const [s, r, c] = await Promise.all([
    parseApi.listEvalSamples().catch(() => []),
    parseApi.listEvalRuns().catch(() => []),
    parseApi.getCalibration().catch(() => null),
  ])
  if (disposed) return
  samples.value = s
  runs.value = r
  calibration.value = c
  if (!current.value && r.length) current.value = r[0]
  poll()
}
onMounted(load)
onBeforeUnmount(() => {
  disposed = true
  if (timer) clearTimeout(timer)
})

function poll() {
  if (timer) clearTimeout(timer)
  if (disposed) return
  const run = current.value
  if (!run || !['queued', 'running'].includes(run.status)) return
  timer = setTimeout(async () => {
    const next = await parseApi.getEvalRun(run.id).catch(() => null)
    if (disposed) return
    if (next && current.value?.id === run.id) {
      current.value = next
      runs.value = runs.value.map((r) => (r.id === next.id ? next : r))
    }
    poll()
  }, 2000)
}

async function start() {
  error.value = ''
  notice.value = ''
  try {
    const run = await parseApi.createEvalRun()
    runs.value = [run, ...runs.value]
    current.value = run
    poll()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function removeSample(x: EvalSample) {
  if (!confirm(`移除评测样本「${x.fileName}」？`)) return
  await parseApi.deleteEvalSample(x.id).catch((e) => (error.value = e.message))
  samples.value = samples.value.filter((s) => s.id !== x.id)
}

async function applyCal() {
  if (!current.value) return
  try {
    calibration.value = await parseApi.applyCalibration(current.value.id)
    notice.value = '已采用难度校准，之后解析的试卷生效'
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function clearCal() {
  await parseApi.clearCalibration().catch((e) => (error.value = e.message))
  calibration.value = null
}

type MetricKey = keyof EvalMetrics
const METRICS: { key: MetricKey; label: string; target?: number; lowerBetter?: boolean; hint: string }[] = [
  { key: 'splitRecall', label: '拆题查全率', target: 0.9, hint: '标准题中被正确拆出的比例' },
  { key: 'splitPrecision', label: '拆题查准率', target: 0.9, hint: '拆出的题中能与标准题配对的比例' },
  { key: 'typeAccuracy', label: '题型准确率', hint: '配对成功的题中题型一致的比例' },
  { key: 'optionAccuracy', label: '选项数准确率', hint: '选择题选项数一致的比例' },
  { key: 'answerAccuracy', label: '答案准确率', hint: '只统计原卷或老师填写的答案' },
  { key: 'knowledgeTop3', label: '知识点前 3 命中率', target: 0.8, hint: '前 3 个知识点中至少命中 1 个标准知识点的题目比例（验收线 80%）' },
  { key: 'knowledgeRecall', label: '知识点召回率', hint: '标准知识点被前 3 个知识点覆盖的比例' },
  { key: 'difficultyMae', label: '难度平均误差', lowerBetter: true, hint: '只统计老师调整过难度的题，越低越好' },
  { key: 'metaAccuracy', label: '试卷分类准确率', hint: '学段、学科、年级、试卷类型' },
]
const fmt = (v: number | null | undefined, mae = false) => (v == null ? '—' : mae ? v.toFixed(2) : Math.round(v * 100) + '%')
const status = (m: (typeof METRICS)[number], v: number | null | undefined) =>
  v == null || m.target == null ? '' : v >= m.target ? 'pass' : 'fail'

const DATE = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const running = computed(() => current.value && ['queued', 'running'].includes(current.value.status))
</script>

<template>
  <main class="ev container">
    <div class="top">
      <div>
        <RouterLink to="/upload" class="back">‹ 试卷解析</RouterLink>
        <h1>解析评测</h1>
        <span class="sub">以老师核对后的结果为标准答案，重新解析原文件并逐项对比，用于衡量解析效果、比较不同配置。</span>
      </div>
      <button class="btn btn-primary run" :disabled="!!running || !samples.length" @click="start">
        {{ running ? `评测中 ${current!.done}/${current!.total}` : `运行评测（${samples.length} 份样本）` }}
      </button>
    </div>
    <p v-if="error" class="card err" role="alert">{{ error }}</p>
    <p v-if="notice" class="card ok">{{ notice }}</p>

    <div class="row">
      <aside class="side">
        <div class="card panel">
          <span class="card-title">评测样本</span>
          <p class="muted small">在核对页把核对好的试卷「设为评测样本」。建议覆盖电子版、扫描件、双栏、答案附在卷末等情况，每类 3 份以上。</p>
          <ul class="list">
            <li v-for="x in samples" :key="x.id">
              <RouterLink :to="{ path: '/upload', query: { job: x.jobId } }" class="name" :title="x.fileName">{{ x.fileName }}</RouterLink>
              <span class="meta">{{ x.questionCount }} 题</span>
              <button class="btn-link small" @click="removeSample(x)">移除</button>
            </li>
          </ul>
          <p v-if="!samples.length" class="muted small">还没有评测样本</p>
        </div>
        <div class="card panel">
          <span class="card-title">历史评测</span>
          <ul class="list">
            <li v-for="r in runs" :key="r.id" :class="{ on: current?.id === r.id }">
              <button class="name btn-plain" @click="current = r; poll()">{{ DATE.format(new Date(r.createdAt)) }} · {{ r.total }} 份</button>
              <span class="meta">{{ r.status === 'done' ? '命中 ' + fmt(r.metrics?.knowledgeTop3) : r.status === 'failed' ? '失败' : '进行中' }}</span>
            </li>
          </ul>
          <p v-if="!runs.length" class="muted small">暂无</p>
        </div>
        <div class="card panel">
          <span class="card-title">难度校准</span>
          <p v-if="calibration" class="small">当前：难度 = {{ calibration.a }} × 评估值 {{ calibration.b >= 0 ? '+' : '−' }} {{ Math.abs(calibration.b) }}<br><span class="muted">{{ calibration.source }}</span></p>
          <p v-else class="muted small">未启用。评测中老师调整过难度的题达到 5 道后，可拟合校准直线。</p>
          <button v-if="calibration" class="btn-link small" @click="clearCal">取消校准</button>
        </div>
      </aside>

      <section class="main-col">
        <div v-if="!current" class="card panel muted">还没有评测记录</div>
        <template v-else>
          <div class="card panel">
            <div class="head">
              <span class="card-title">总体指标</span>
              <span class="muted small">
                {{ current.config.llmModel ?? '未启用大模型' }} · {{ current.config.mineruModel ? 'MinerU ' + current.config.mineruModel : '未启用 MinerU' }}
                <template v-if="current.config.embeddingModel"> · {{ current.config.embeddingModel }}</template>
              </span>
            </div>
            <p v-if="current.error" class="err-text">{{ current.error }}</p>
            <div v-if="current.metrics" class="metrics">
              <div v-for="m in METRICS" :key="m.key" class="metric" :class="status(m, current.metrics[m.key])" :title="m.hint">
                <span class="m-label">{{ m.label }}</span>
                <b>{{ fmt(current.metrics[m.key], m.lowerBetter) }}</b>
                <span v-if="m.target" class="m-target">目标 ≥ {{ m.target * 100 }}%</span>
              </div>
            </div>
            <p v-else class="muted">评测进行中：{{ current.done }} / {{ current.total }} 份</p>
            <div v-if="current.metrics?.calibration" class="cal">
              <span>拟合难度校准：难度 = {{ current.metrics.calibration.a }} × 评估值 + {{ current.metrics.calibration.b }}（{{ current.metrics.calibration.n }} 道题，误差 {{ current.metrics.calibration.maeBefore }} → {{ current.metrics.calibration.maeAfter }}）</span>
              <button class="btn btn-outline" @click="applyCal">采用校准</button>
            </div>
          </div>

          <div v-for="d in current.details" :key="d.sampleId" class="card panel">
            <div class="head">
              <span class="card-title">{{ d.fileName }}</span>
              <RouterLink v-if="d.jobId" :to="{ path: '/upload', query: { job: d.jobId } }" class="small">查看解析结果</RouterLink>
            </div>
            <p v-if="d.error" class="err-text">{{ d.error }}</p>
            <template v-else-if="d.metrics">
              <div class="chips">
                <span v-for="m in METRICS" :key="m.key" :class="status(m, d.metrics[m.key])">{{ m.label }} {{ fmt(d.metrics[m.key], m.lowerBetter) }}</span>
              </div>
              <p v-if="d.unmatchedGold?.length" class="small warn">未拆出的题：第 {{ d.unmatchedGold.join('、') }} 题</p>
              <p v-if="d.extraPred?.length" class="small warn">多拆出的题：解析结果第 {{ d.extraPred.join('、') }} 题</p>
              <ul class="issues">
                <li v-for="q in d.questions?.filter((x) => x.issues.length)" :key="q.no">
                  <b>第 {{ q.no }} 题</b>{{ q.issues.join('；') }}
                </li>
              </ul>
            </template>
          </div>
        </template>
      </section>
    </div>
  </main>
</template>

<style scoped>
.ev { width: 100%; padding-top: 24px; padding-bottom: 56px; display: flex; flex-direction: column; gap: 16px; }
.top { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 12px; }
.back { font-size: 13px; color: var(--c-text-3); }
h1 { margin: 4px 0 6px; font-size: 24px; }
.sub { font-size: 14px; color: var(--c-text-3); }
.run { height: 40px; font-size: 14px; padding: 0 18px; }
.run:disabled { opacity: .6; cursor: not-allowed; }
.err, .ok { margin: 0; padding: 10px 16px; font-size: 13px; }
.err { color: #A0301F; background: #FBEAE6; border-color: #EFC2B8; }
.ok { color: #3F7340; background: #E9F1E7; border-color: #C8DCC4; }
.err-text { color: #A0301F; font-size: 13px; margin: 0; }
.row { display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; }
.side { flex: 1 0 280px; max-width: 340px; display: flex; flex-direction: column; gap: 14px; }
.main-col { flex: 999 1 480px; min-width: 0; display: flex; flex-direction: column; gap: 14px; }
.panel { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
.small { font-size: 12px; margin: 0; line-height: 1.6; }
.warn { color: #8F4115; }
.list { list-style: none; margin: 0; padding: 0; }
.list li { display: flex; align-items: center; gap: 8px; padding: 7px 0; border-top: 1px solid var(--c-divider); font-size: 13px; }
.list li:first-child { border-top: none; }
.list li.on .name { color: var(--c-primary); font-weight: 600; }
.name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-ink); text-align: left; }
.btn-plain { border: none; background: none; padding: 0; font-size: 13px; }
.meta { font-size: 12px; color: var(--c-text-4); flex-shrink: 0; }
.head { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; align-items: baseline; }
.metrics { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.metric { border: 1px solid var(--c-border); border-radius: var(--r-md); padding: 10px 12px; display: flex; flex-direction: column; gap: 2px; cursor: help; }
.metric b { font-size: 22px; font-variant-numeric: tabular-nums; }
.m-label { font-size: 12px; color: var(--c-text-3); }
.m-target { font-size: 11px; color: var(--c-text-4); }
.metric.pass { border-color: #C8DCC4; background: #F3F8F2; }
.metric.pass b { color: #3F7340; }
.metric.fail { border-color: #EFC2B8; background: #FDF5F3; }
.metric.fail b { color: #A0301F; }
.cal { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; font-size: 13px; background: var(--c-surface-2); border-radius: var(--r-md); padding: 10px 12px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips span { font-size: 12px; background: var(--c-paper); border-radius: 4px; padding: 2px 8px; color: var(--c-text-2); }
.chips .pass { background: #E9F1E7; color: #3F7340; }
.chips .fail { background: #FBEAE6; color: #A0301F; }
.issues { margin: 0; padding-left: 18px; font-size: 13px; color: var(--c-text-2); display: flex; flex-direction: column; gap: 4px; }
.issues b { margin-right: 8px; }
</style>
