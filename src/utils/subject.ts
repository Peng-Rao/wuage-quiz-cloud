import { computed, inject, provide, toValue, type ComputedRef, type InjectionKey, type MaybeRefOrGetter } from 'vue'
import { textWidth } from './richtext'

/**
 * 按学科区分的题目排版：语文、英语有阅读材料、诗文、对话等结构，按卷面习惯专门排版；
 * 其他学科（数学、理化等）保持原样，只渲染公式。
 */
export type TextLayout = 'zh' | 'en' | 'plain'

export const layoutOf = (subject?: string | null): TextLayout =>
  subject === '语文' ? 'zh' : subject === '英语' ? 'en' : 'plain'

const KEY: InjectionKey<ComputedRef<TextLayout>> = Symbol('text-layout')

/** 题目卡片等容器声明所属学科，内部的 MathText 按该学科排版 */
export function provideSubject(subject: MaybeRefOrGetter<string | null | undefined>): ComputedRef<TextLayout> {
  const layout = computed(() => layoutOf(toValue(subject)))
  provide(KEY, layout)
  return layout
}

export function useTextLayout(): ComputedRef<TextLayout> {
  return inject(KEY, null) ?? computed(() => 'plain' as const)
}

const LETTERS = 'ABCDEFGH'

/** 选项标号：英语卷用半角「A.」，其余用全角「A．」 */
export const optionLabel = (layout: TextLayout, i: number) => (layout === 'en' ? `${LETTERS[i]}. ` : `${LETTERS[i]}．`)

/** 题号后的分隔符 */
export const noSep = (layout: TextLayout) => (layout === 'en' ? '. ' : '．')

/** 选项每行几个：按最长选项的大致宽度（字）排成 4 / 2 / 1 列，与 Word 导出一致 */
export function optionCols(options: string[]): 1 | 2 | 4 {
  const max = Math.max(0, ...options.map(textWidth)) + 2
  return max <= 9 ? 4 : max <= 19 ? 2 : 1
}
