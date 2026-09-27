import type { QuestionSource } from '@/api/parse'

/** 题目来源的简写，如「26-27高一上·北京·期中」 */
export function shortSource(s: QuestionSource | null | undefined): string {
  if (!s) return ''
  const years = s.schoolYear.match(/(?:19|20)\d{2}/g) ?? s.title.match(/(?:19|20)\d{2}/g) ?? []
  const yy = years.slice(0, 2).map((y) => y.slice(2)).join('-')
  const term = (`${s.schoolYear} ${s.title}`.match(/\d{4}\s*(上|下)|(上|下)(?:学期|册)/) ?? []).slice(1).find(Boolean) ?? ''
  const head = `${yy}${s.grade}${term}`
  const region = s.region.split(/\s*[·•/]\s*/)[0]
  const type = s.paperType.replace(/考试$/, '')
  return [head, region, type].filter(Boolean).join('·')
}

/** 入库时间：今日 / 昨日 / 月-日 / 年-月-日 */
export function dateLabel(iso: string, now = new Date()): string {
  const d = new Date(iso)
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((day(now) - day(d)) / 86400000)
  if (diff === 0) return '今日'
  if (diff === 1) return '昨日'
  const md = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}-${md}`
}
