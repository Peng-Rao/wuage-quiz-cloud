import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { QUESTIONS } from '@/data/mock'

/** 题库题目用数字 id，试卷解析得到的草稿题用字符串 id */
export type BasketId = number | string

const STORAGE_KEY = 'fg-basket'
const DEFAULT_IDS: BasketId[] = [1, 3, 6]

function load(): BasketId[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const v = raw ? JSON.parse(raw) : null
    return Array.isArray(v) ? v : DEFAULT_IDS
  } catch {
    return DEFAULT_IDS
  }
}

export const useBasketStore = defineStore('basket', () => {
  const ids = ref<BasketId[]>(load())

  watch(ids, (v) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(v))
    } catch {
      // 隐私模式等场景下无法写入，忽略
    }
  }, { deep: true })

  const count = computed(() => ids.value.length)
  /** 篮中可排版的题库题目，按加入顺序 */
  const questions = computed(() =>
    ids.value.flatMap((id) => QUESTIONS.filter((q) => q.id === id)),
  )

  const has = (id: BasketId) => ids.value.includes(id)

  function toggle(id: BasketId) {
    ids.value = has(id) ? ids.value.filter((x) => x !== id) : [...ids.value, id]
  }

  function addMany(newIds: BasketId[]) {
    ids.value = [...new Set([...ids.value, ...newIds])]
  }

  function clear() {
    ids.value = []
  }

  return { ids, count, questions, has, toggle, addMany, clear }
})
