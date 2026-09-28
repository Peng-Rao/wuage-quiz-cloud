/** 题目文本中的公式：$...$ 为行内公式，$$...$$ 为独立公式（MinerU 输出格式） */
export const MATH_RE = /(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g

export interface TextSegment {
  /** 普通文字或公式的 LaTeX（不含 $） */
  text: string
  math: boolean
  /** 独立公式（$$...$$） */
  display: boolean
}

/** MinerU 输出 Markdown，填空横线等被转义成 \_；公式外的转义还原成原字符（公式内的 \_ 是合法 LaTeX） */
const MD_ESCAPE_RE = /\\([_*#`~|<>[\]])/g

export function splitMath(text: string): TextSegment[] {
  // split 带捕获组时，奇数位是公式
  return text.split(MATH_RE).map((part, i) => {
    if (i % 2 === 0) return { text: part.replace(MD_ESCAPE_RE, '$1'), math: false, display: false }
    const display = part.startsWith('$$')
    return { text: part.slice(display ? 2 : 1, display ? -2 : -1), math: true, display }
  }).filter((s) => s.text !== '')
}

/** 去掉公式定界符的纯文本，用于摘要、字数估算 */
export function plainText(text: string): string {
  return splitMath(text).map((s) => s.text).join('').replace(/\s+/g, ' ').trim()
}

/** 光标所在的行内公式：返回公式在文本中的起止位置（含 $）与 LaTeX；光标不在公式内时为 null */
export function mathAt(text: string, caret: number): { start: number; end: number; tex: string } | null {
  for (const m of text.matchAll(MATH_RE)) {
    const start = m.index!, end = start + m[0].length
    if (caret > start && caret < end) {
      const display = m[0].startsWith('$$')
      return { start, end, tex: m[0].slice(display ? 2 : 1, display ? -2 : -1) }
    }
  }
  return null
}
