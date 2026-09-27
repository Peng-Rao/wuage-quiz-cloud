import type { UsageSummary } from '@/api/parse'

export const PURPOSE_LABELS: Record<string, string> = {
  parse: '版面识别',
  classify: '试卷分类',
  segment: '拆题',
  answer: 'AI 解答',
  compose: 'AI 组卷',
}

/** 12345 → 1.2 万；小于 1 万原样显示 */
export function formatTokens(n: number): string {
  if (n >= 10000) return (n / 10000).toFixed(n >= 100000 ? 0 : 1) + ' 万'
  return n.toLocaleString('zh-CN')
}

/** 金额较小时保留更多小数，避免显示成 0.00 */
export function formatCost(v: number | null | undefined, currency = '¥'): string {
  if (v == null) return '—'
  const digits = v === 0 ? 2 : v < 0.01 ? 4 : v < 1 ? 3 : 2
  return currency + v.toFixed(digits)
}

export function formatDuration(ms: number): string {
  const s = Math.round(ms / 1000)
  return s >= 60 ? `${Math.floor(s / 60)} 分 ${s % 60} 秒` : `${s} 秒`
}

/** 单价缺失时给出的说明 */
export function pricingNote(u: UsageSummary): string {
  if (u.cost == null) return '未配置单价，仅统计用量'
  if (!u.priced) return `未配置单价：${u.unpricedModels.join('、')}，费用未计入`
  return ''
}
