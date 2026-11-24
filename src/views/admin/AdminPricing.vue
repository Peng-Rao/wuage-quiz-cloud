<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { adminApi, type PriceStatus } from '@/api/admin'
import type { ModelPrice, PriceTier } from '@/api/usage'
import LoadingState from '@/components/LoadingState.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import { SOURCE_LABELS, formatPercent } from '@/utils/usage'
import { fmtTime } from './labels'

const prices = ref<ModelPrice[] | null>(null)
const status = ref<PriceStatus | null>(null)
const history = ref(false)
const error = ref('')
const busy = ref(false)
const message = ref('')
const refreshLines = ref<string[]>([])

async function load() {
  try {
    const [p, st] = await Promise.all([adminApi.prices(history.value), adminApi.priceStatus()])
    prices.value = p; status.value = st; error.value = ''
  } catch (e) { error.value = (e as Error).message }
}
onMounted(load)

const SYMBOL: Record<string, string> = { CNY: '¥', USD: '$' }
const sorted = computed(() => [...(prices.value ?? [])].sort((a, b) =>
  a.provider.localeCompare(b.provider) || a.model.localeCompare(b.model) || Number(b.billing) - Number(a.billing)
  || a.source.localeCompare(b.source) || b.fetchedAt.localeCompare(a.fetchedAt)))
const missing = computed(() => (status.value?.models ?? []).filter(m => !prices.value?.some(p => p.model === m && p.billing)))

function tierText(t: PriceTier, cur: string) {
  const s = SYMBOL[cur] ?? cur + ' '
  if (t.per_page != null) return `${s}${t.per_page} / 页`
  const range = t.max_input ? `≤${(t.max_input / 1000).toLocaleString()}K：` : ''
  const off = t.input_offpeak != null ? `（闲时 ${s}${t.input_offpeak} / ${s}${t.output_offpeak}）` : ''
  const mode = t.mode ? ` [${t.mode === 'thinking' ? '思考' : '非思考'}]` : ''
  return `${range}${s}${t.input} / ${s}${t.output}${off}${mode}`
}
function cachedText(p: ModelPrice) {
  const t = p.tiers[0] ?? {}
  if (t.per_page != null) return '—'
  if (t.cached_input != null) return `${SYMBOL[p.currency] ?? ''}${t.cached_input}`
  return p.cacheRatio != null ? `输入 × ${formatPercent(p.cacheRatio, 0)}` : '—'
}
function peakText(p: ModelPrice) {
  if (!p.peak) return '不分峰谷'
  return `${p.peak.weekdays_only ? '工作日 ' : ''}${p.peak.ranges.map(([a, b]) => `${a}–${b}`).join('、')} 点`
}

async function run(fn: () => Promise<unknown>) {
  busy.value = true; message.value = ''; error.value = ''
  try { await fn(); await load() } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
const refresh = () => run(async () => {
  const r = await adminApi.refreshPrices()
  refreshLines.value = r.lines
  message.value = `已抓取，共 ${r.lines.length} 条结果`
})
function clearManual(p: ModelPrice) {
  if (!window.confirm(`删除「${p.model}」的全部手动单价？之后的调用按计费来源的抓取单价计价；历史调用需「重新计价」才会改变。`)) return
  run(async () => { await adminApi.clearManualPrice(p.provider, p.model); message.value = '手动单价已删除' })
}

// ---- 设置手动单价 ----
interface TierRow { max_input: string; input: string; output: string; cached_input: string; input_offpeak: string; output_offpeak: string }
const blankTier = (): TierRow => ({ max_input: '', input: '', output: '', cached_input: '', input_offpeak: '', output_offpeak: '' })
const editOpen = ref(false)
const editError = ref('')
const form = reactive({ provider: 'llm' as 'llm' | 'mineru', model: '', currency: 'CNY' as 'CNY' | 'USD', tiers: [blankTier()], perPage: '', cacheRatio: '', peak: '', weekdaysOnly: true, notes: '' })
const str = (v: number | null | undefined) => (v == null ? '' : String(v))
function openEdit(p?: ModelPrice) {
  editError.value = ''
  const t = p?.tiers ?? []
  Object.assign(form, {
    provider: p?.provider ?? 'llm', model: p?.model ?? '', currency: (p?.currency === 'USD' ? 'USD' : 'CNY'),
    tiers: p?.provider === 'mineru' || !t.length ? [blankTier()] : t.map(x => ({ max_input: str(x.max_input), input: str(x.input), output: str(x.output), cached_input: str(x.cached_input), input_offpeak: str(x.input_offpeak), output_offpeak: str(x.output_offpeak) })),
    perPage: p?.provider === 'mineru' ? str(t[0]?.per_page) : '', cacheRatio: str(p?.cacheRatio),
    peak: p?.peak ? p.peak.ranges.map(([a, b]) => `${a}-${b}`).join(',') : '', weekdaysOnly: p?.peak?.weekdays_only ?? true, notes: '',
  })
  editOpen.value = true
}
// type="number" 的 v-model 会得到数字，统一按文本处理
const text = (v: unknown) => (v == null ? '' : String(v).trim())
const num = (v: unknown) => (text(v) === '' ? null : Number(v))
async function saveEdit() {
  editError.value = ''
  let peak = null
  if (text(form.peak)) {
    const ranges = form.peak.split(/[,，、\s]+/).filter(Boolean).map(r => r.split(/[-–~]/).map(Number) as [number, number])
    if (ranges.some(r => r.length !== 2 || r.some(Number.isNaN))) { editError.value = '高峰时段格式如 9-12,14-18'; return }
    peak = { ranges, weekdays_only: form.weekdaysOnly }
  }
  try {
    await adminApi.setPrice({
      provider: form.provider, model: form.model, currency: form.currency, notes: form.notes, peak,
      cacheRatio: num(form.cacheRatio),
      perPage: form.provider === 'mineru' ? num(form.perPage) : null,
      tiers: form.provider === 'mineru' ? [] : form.tiers.filter(t => text(t.input) || text(t.output)).map(t => ({
        max_input: num(t.max_input), input: Number(t.input), output: Number(t.output), cached_input: num(t.cached_input),
        input_offpeak: num(t.input_offpeak), output_offpeak: num(t.output_offpeak),
      })),
    })
    editOpen.value = false
    message.value = `已设置「${form.provider === 'mineru' ? 'MinerU' : form.model}」的手动单价，之后的调用按此计价`
    await load()
  } catch (e) { editError.value = (e as Error).message }
}

// ---- 重新计价 ----
const recOpen = ref(false)
const rec = reactive({ mode: 'missing' as 'missing' | 'since' | 'all', since: '' })
async function recompute() {
  const body = rec.mode === 'all' ? { all: true } : rec.mode === 'since' ? { since: rec.since ? new Date(rec.since + 'T00:00:00+08:00').toISOString() : null } : {}
  if (rec.mode === 'since' && !rec.since) return
  recOpen.value = false
  await run(async () => { const r = await adminApi.recompute(body); message.value = `已重新计价 ${r.priced} 次调用，${r.missing} 次仍缺单价` })
}
</script>

<template>
  <main class="adm-page">
    <div class="adm-head">
      <div><h1>模型单价</h1><p>每次 AI 调用按当时的单价计价：手动单价优先，其次「计费来源」（当前调用平台）抓取的单价，其余来源仅作参考。</p></div>
      <div class="adm-actions">
        <button class="btn btn-sm" :disabled="busy" @click="refresh">{{ busy ? '处理中…' : '立即抓取单价' }}</button>
        <button class="btn btn-sm" :disabled="busy" @click="recOpen = true">重新计价…</button>
        <button class="btn btn-primary btn-sm" :disabled="busy" @click="openEdit()">＋ 设置手动单价</button>
      </div>
    </div>
    <p v-if="error" class="adm-error" role="alert">{{ error }}</p>
    <p v-if="message" class="adm-ok" role="status">{{ message }}</p>
    <LoadingState v-if="!prices && !error" />
    <template v-else-if="prices && status">
      <section class="adm-card meta">
        <dl class="adm-kv">
          <dt>计费来源</dt><dd><b>{{ SOURCE_LABELS[status.billingSource] ?? status.billingSource }}</b>（查找顺序：{{ status.costSources.map(s => SOURCE_LABELS[s] ?? s).join(' → ') }}）</dd>
          <dt>自动抓取</dt><dd>{{ status.refreshHours ? `每 ${status.refreshHours} 小时` : '已关闭' }} · 来源 {{ status.sources.map(s => SOURCE_LABELS[s] ?? s).join('、') }} · 最近 {{ fmtTime(status.checkedAt) }}</dd>
          <dt>需要单价的模型</dt><dd>{{ status.models.join('、') || '—' }}<span v-if="missing.length" class="adm-badge warn miss">缺少计费单价：{{ missing.join('、') }}</span></dd>
        </dl>
        <p class="adm-hint">计费来源与抓取间隔可在「密钥配置 · 成本计价」中修改。</p>
        <details v-if="refreshLines.length" class="lines"><summary>本次抓取结果（* 为计费单价）</summary><pre>{{ refreshLines.join('\n') }}</pre></details>
      </section>

      <section class="adm-card">
        <h2>{{ history ? '全部单价记录' : '当前单价' }} <small>单位：每百万 tokens（输入 / 输出）</small>
          <label class="adm-check right"><input v-model="history" type="checkbox" @change="load">显示历史记录</label>
        </h2>
        <div class="adm-table-wrap">
          <table class="adm-table">
            <thead><tr><th>模型</th><th>来源</th><th>单价</th><th>缓存命中</th><th>峰谷时段</th><th>生效于</th><th></th></tr></thead>
            <tbody>
              <tr v-for="p in sorted" :key="p.id" :class="{ dim: !p.billing }">
                <td><b>{{ p.provider === 'mineru' ? 'MinerU' : p.model }}</b><span v-if="p.notes" class="sub">{{ p.notes }}</span></td>
                <td><span class="adm-badge" :class="p.billing ? 'ok' : ''">{{ SOURCE_LABELS[p.source] ?? p.source }}{{ p.billing ? ' · 计费' : '' }}</span></td>
                <td class="tiers"><span v-for="(t, i) in p.tiers" :key="i">{{ tierText(t, p.currency) }}</span></td>
                <td>{{ cachedText(p) }}</td>
                <td>{{ peakText(p) }}</td>
                <td>{{ fmtTime(p.fetchedAt) }}<span class="sub">核对 {{ fmtTime(p.checkedAt) }}</span></td>
                <td class="ops">
                  <button class="btn-link is-primary" :disabled="busy" @click="openEdit(p)">设为手动价</button>
                  <button v-if="p.source === 'manual' && !history" class="btn-link danger" :disabled="busy" @click="clearManual(p)">删除手动价</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!prices.length" class="adm-empty">还没有单价记录，点击「立即抓取单价」或手动设置</p>
        </div>
      </section>
    </template>

    <ModalDialog v-model="editOpen" title="设置手动单价" :width="720">
      <form id="price-form" class="adm-form" @submit.prevent="saveEdit">
        <div class="adm-grid-3">
          <label>类型<select v-model="form.provider"><option value="llm">大模型</option><option value="mineru">MinerU（按页）</option></select></label>
          <label v-if="form.provider === 'llm'">模型<input v-model="form.model" required maxlength="64" list="price-models" placeholder="与调用时的模型名一致"><datalist id="price-models"><option v-for="m in status?.models ?? []" :key="m" :value="m" /></datalist></label>
          <label>币种<select v-model="form.currency"><option value="CNY">人民币 ¥</option><option value="USD">美元 $</option></select></label>
        </div>
        <label v-if="form.provider === 'mineru'">每页单价<input v-model="form.perPage" type="number" min="0" step="any" required></label>
        <template v-else>
          <div class="tier-head"><span>阶梯单价（每百万 tokens；按单次请求的输入 tokens 落在哪一档计价，最后一档上限留空）</span><button type="button" class="btn-link is-primary" @click="form.tiers.push(blankTier())">＋ 增加一档</button></div>
          <div class="adm-table-wrap">
            <table class="adm-table tier-table">
              <thead><tr><th>输入上限</th><th>输入</th><th>输出</th><th>缓存命中</th><th>闲时输入</th><th>闲时输出</th><th></th></tr></thead>
              <tbody>
                <tr v-for="(t, i) in form.tiers" :key="i">
                  <td><input v-model="t.max_input" type="number" min="1" step="1" placeholder="不限" aria-label="输入上限"></td>
                  <td><input v-model="t.input" type="number" min="0" step="any" required aria-label="输入单价"></td>
                  <td><input v-model="t.output" type="number" min="0" step="any" required aria-label="输出单价"></td>
                  <td><input v-model="t.cached_input" type="number" min="0" step="any" placeholder="—" aria-label="缓存命中单价"></td>
                  <td><input v-model="t.input_offpeak" type="number" min="0" step="any" placeholder="—" aria-label="闲时输入单价"></td>
                  <td><input v-model="t.output_offpeak" type="number" min="0" step="any" placeholder="—" aria-label="闲时输出单价"></td>
                  <td><button type="button" class="btn-link" :disabled="form.tiers.length === 1" aria-label="删除这一档" @click="form.tiers.splice(i, 1)">×</button></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="adm-grid-3">
            <label>缓存命中比例<input v-model="form.cacheRatio" type="number" min="0" max="1" step="any" placeholder="如 0.2，未填缓存单价时使用"></label>
            <label>高峰时段（北京时间）<input v-model="form.peak" placeholder="如 9-12,14-18；留空为不分峰谷"></label>
            <label class="check peak-check"><input v-model="form.weekdaysOnly" type="checkbox">仅工作日分峰谷</label>
          </div>
        </template>
        <label>备注<input v-model="form.notes" maxlength="200" placeholder="如：合同价、2026 年 11 月调价"></label>
        <p class="adm-hint">手动单价立即用于之后的调用；已发生的调用需「重新计价」。定时抓取不会覆盖后台设置的手动单价。</p>
        <p v-if="editError" class="adm-error" role="alert">{{ editError }}</p>
      </form>
      <template #footer>
        <button class="btn btn-sm" type="button" @click="editOpen = false">取消</button>
        <button class="btn btn-primary btn-sm" form="price-form">保存单价</button>
      </template>
    </ModalDialog>

    <ModalDialog v-model="recOpen" title="重新计价" :width="460">
      <form id="rec-form" class="adm-form" @submit.prevent="recompute">
        <label class="check"><input v-model="rec.mode" type="radio" value="missing">只为尚未计价的调用补算</label>
        <label class="check"><input v-model="rec.mode" type="radio" value="since">重新计算某天之后的全部调用</label>
        <input v-if="rec.mode === 'since'" v-model="rec.since" type="date" required aria-label="开始日期">
        <label class="check"><input v-model="rec.mode" type="radio" value="all">重新计算全部历史调用</label>
        <p class="adm-hint">每次调用按其发生时有效的单价计算（手动单价优先，其次计费来源）。</p>
      </form>
      <template #footer>
        <button class="btn btn-sm" type="button" @click="recOpen = false">取消</button>
        <button class="btn btn-primary btn-sm" form="rec-form">开始计价</button>
      </template>
    </ModalDialog>
  </main>
</template>

<style scoped>
.meta { margin-bottom: 16px; }
.miss { margin-left: 8px; white-space: normal; }
.lines { margin-top: 12px; font-size: 13px; }
.lines pre { background: var(--c-paper-2); padding: 12px; border-radius: var(--r-sm); overflow-x: auto; font-size: 12px; line-height: 1.6; }
.right { margin-left: auto; font-weight: 400; }
.dim td { color: var(--c-text-3); }
.tiers span { display: block; white-space: nowrap; }
.ops { white-space: nowrap; text-align: right; }
.ops .btn-link + .btn-link { margin-left: 10px; }
.danger { color: var(--c-danger); }
.tier-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 13px; color: var(--c-text-2); }
.tier-table td { padding: 6px 4px; }
.tier-table input { min-width: 80px; padding: 6px 8px !important; }
.peak-check { align-self: end; padding-bottom: 10px; }
</style>
