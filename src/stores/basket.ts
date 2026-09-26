import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { SCORE, TYPE_ORDER } from '@/data/mock'
import type { BankQuestion } from '@/api/bank'
import type { DraftQuestion, QuestionType } from '@/api/parse'

/**
 * 试题篮中的题目快照：加入时复制题目内容，组卷排版不再依赖来源页面。
 * 题库题目与试卷解析得到的草稿题都转换为这一结构。
 */
export interface BasketQuestion {
  id: string
  type: QuestionType
  stem: string
  /** 选项，不含「A．」前缀 */
  options: string[]
  answer: string | null
  analysis: string | null
  /** 难度系数 0–1，越高越难 */
  coef: number
  knowledge: string[]
  images: string[]
  /** 出处 */
  source: string
  /** 原卷分值，作为默认分值 */
  score: number
}

export interface BasketItem {
  q: BasketQuestion
  /** 组卷时的分值 */
  score: number
}

export interface BasketSection {
  type: QuestionType
  items: BasketItem[]
  score: number
  /** 各题分值相同时为该分值，否则为 null */
  each: number | null
}

export function fromBank(q: BankQuestion): BasketQuestion {
  return {
    id: q.id, type: q.type, stem: q.stem, options: q.options, answer: q.answer, analysis: q.analysis, coef: q.coef,
    knowledge: q.knowledgePoints.map((k) => k.name), images: q.images, source: q.source?.label ?? '', score: q.score,
  }
}

export function fromDraft(q: DraftQuestion): BasketQuestion {
  return {
    id: q.id, type: q.type, stem: q.stem, options: q.options, answer: q.answer, analysis: q.analysis, coef: q.coef,
    knowledge: q.knowledgePoints.map((k) => k.name), images: q.images, source: q.source?.label ?? '', score: q.score,
  }
}

const defaultScore = (q: BasketQuestion) => (q.score > 0 ? q.score : SCORE[q.type] ?? 5)

const STORAGE_KEY = 'fg-basket-v2'

interface Saved { items: BasketItem[]; typeOrder: QuestionType[] }

function load(): Saved {
  const empty = { items: [], typeOrder: [...TYPE_ORDER] }
  try {
    const v = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')
    if (!v || !Array.isArray(v.items) || !Array.isArray(v.typeOrder)) return empty
    const items = (v.items as BasketItem[]).filter((x) => x?.q?.id && x.q.type && typeof x.score === 'number')
    // 补上缺失的题型，保证每种题型都在排序中
    const order = [...new Set([...(v.typeOrder as QuestionType[]).filter((t) => TYPE_ORDER.includes(t)), ...TYPE_ORDER])]
    return { items, typeOrder: order }
  } catch {
    return empty
  }
}

export const useBasketStore = defineStore('basket', () => {
  const saved = load()
  const items = ref<BasketItem[]>(saved.items)
  /** 大题（题型）顺序 */
  const typeOrder = ref<QuestionType[]>(saved.typeOrder)

  watch([items, typeOrder], () => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ items: items.value, typeOrder: typeOrder.value }))
    } catch {
      // 隐私模式等场景下无法写入，忽略
    }
  }, { deep: true })

  const count = computed(() => items.value.length)
  const totalScore = computed(() => items.value.reduce((a, x) => a + x.score, 0))
  /** 按大题顺序分组；同一题型内按加入 / 调整后的顺序 */
  const sections = computed<BasketSection[]>(() =>
    typeOrder.value
      .map((type) => {
        const list = items.value.filter((x) => x.q.type === type)
        const scores = new Set(list.map((x) => x.score))
        return { type, items: list, score: list.reduce((a, x) => a + x.score, 0), each: scores.size === 1 ? list[0].score : null }
      })
      .filter((s) => s.items.length),
  )

  const has = (id: string) => items.value.some((x) => x.q.id === id)

  function add(q: BasketQuestion, score = defaultScore(q)) {
    if (!has(q.id)) items.value = [...items.value, { q, score }]
  }
  function addMany(qs: BasketQuestion[]) {
    const ids = new Set(items.value.map((x) => x.q.id))
    items.value = [...items.value, ...qs.filter((q) => !ids.has(q.id)).map((q) => ({ q, score: defaultScore(q) }))]
  }
  function remove(id: string) {
    items.value = items.value.filter((x) => x.q.id !== id)
  }
  function toggle(q: BasketQuestion) {
    if (has(q.id)) remove(q.id)
    else add(q)
  }
  function clear() {
    items.value = []
  }
  /** 清空后按给定顺序放入（如整卷组卷），大题顺序按题型首次出现的顺序 */
  function replace(qs: BasketQuestion[]) {
    items.value = []
    addMany(qs)
    const seen = [...new Set(qs.map((q) => q.type))]
    typeOrder.value = [...seen, ...TYPE_ORDER.filter((t) => !seen.includes(t))]
  }

  /** 同一大题内把第 from 题移到第 to 题的位置；其余题型位置不变 */
  function reorder(type: QuestionType, from: number, to: number) {
    const list = items.value.filter((x) => x.q.type === type)
    if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return
    const [moved] = list.splice(from, 1)
    list.splice(to, 0, moved)
    let i = 0
    items.value = items.value.map((x) => (x.q.type === type ? list[i++] : x))
  }
  function move(id: string, delta: number) {
    const it = items.value.find((x) => x.q.id === id)
    if (!it) return
    const idx = items.value.filter((x) => x.q.type === it.q.type).indexOf(it)
    reorder(it.q.type, idx, idx + delta)
  }
  /** 大题上移 / 下移（跳过篮中没有的题型） */
  function moveSection(type: QuestionType, delta: number) {
    const present = sections.value.map((s) => s.type)
    const i = present.indexOf(type)
    const other = present[i + delta]
    if (i < 0 || !other) return
    const order = [...typeOrder.value]
    const a = order.indexOf(type), b = order.indexOf(other)
    ;[order[a], order[b]] = [order[b], order[a]]
    typeOrder.value = order
  }

  const clampScore = (n: number) => Math.min(200, Math.max(0, Math.round(n * 2) / 2))
  function setScore(id: string, score: number) {
    if (!Number.isFinite(score)) return
    items.value = items.value.map((x) => (x.q.id === id ? { ...x, score: clampScore(score) } : x))
  }
  /** 大题内每题设为同一分值 */
  function setSectionScore(type: QuestionType, score: number) {
    if (!Number.isFinite(score)) return
    items.value = items.value.map((x) => (x.q.type === type ? { ...x, score: clampScore(score) } : x))
  }
  /** 恢复为原卷分值 */
  function resetScores() {
    items.value = items.value.map((x) => ({ ...x, score: defaultScore(x.q) }))
  }

  return {
    items, typeOrder, count, totalScore, sections, has, add, addMany, remove, toggle, clear, replace,
    reorder, move, moveSection, setScore, setSectionScore, resetScores,
  }
})
