import katex from 'katex'
import { splitMath } from './math'
import type { TextLayout } from './subject'

/**
 * 语文、英语题目文本的结构化排版。
 *
 * 题目文本是解析得到的纯文本（行内公式 $...$，换行分段，填空为 ____），这里按行识别卷面结构：
 * 阅读材料的正文段落、标题与作者行、古诗词、小问与对话，以及行内的填空横线、完形填空编号空、音标。
 * 网页（MathText）与 Word 导出共用同一份解析结果。
 */

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'math'; tex: string; display: boolean }
  /** 填空横线；label 为完形填空的空号 */
  | { kind: 'blank'; width: number; label?: string }
  /** 英语音标，如 /ɪmˈpɔːtnt/ */
  | { kind: 'ipa'; text: string }

/**
 * para 正文段落（首行缩进两字）/ plain 普通行 / sub 小问、选项行（悬挂缩进）/ turn 对话（悬挂缩进）
 * verse 诗词句（居中）/ title 材料标题（居中）/ byline 作者、出处（居中）/ display 独立公式
 */
export type LineRole = 'para' | 'plain' | 'sub' | 'turn' | 'verse' | 'title' | 'byline' | 'display'

export interface Line {
  role: LineRole
  inlines: Inline[]
}

// ---------- 宽度估算 ----------

const WIDE_RE = /[⺀-鿿豈-﫿＀-￯　-〿①-⓿]/

/** 文本的大致宽度（字）：汉字与全角符号记 1，西文记 0.55，公式按 LaTeX 去掉命令后估算 */
export function textWidth(text: string): number {
  return splitMath(text).reduce((a, s) => {
    if (s.math) return a + s.text.replace(/\\[a-zA-Z]+|[{}^_\s]/g, '').length * 0.6
    let w = 0
    for (const ch of s.text) w += WIDE_RE.test(ch) ? 1 : 0.55
    return a + w
  }, 0)
}

// ---------- 行内 ----------

/** 完形填空编号空：__41__、___ 41 ___ */
const NUMBERED_BLANK = String.raw`_+\s*(\d{1,3})\s*_+`
/** 填空横线：两个以上下划线（含全角）；英语卷中单独的 _ 也是空 */
const BLANK = String.raw`_{2,}|＿{2,}`
const SINGLE_BLANK = String.raw`(?<![\w_])_(?![\w_])`
/** 含音标字符的 /.../ */
const IPA = String.raw`\/[^/\n]*[ɪəʊæɑɔʌθðŋʃʒˈˌːɜɒ][^/\n]*\/`

const INLINE_RE: Record<Exclude<TextLayout, 'plain'>, RegExp> = {
  zh: new RegExp(`${NUMBERED_BLANK}|(${BLANK})`, 'g'),
  en: new RegExp(`${NUMBERED_BLANK}|(${BLANK}|${SINGLE_BLANK})|(${IPA})`, 'g'),
}

/** 横线宽度（字）：解析出的横线长度多为固定的 ____，按学科给出够写的宽度 */
const BLANK_WIDTH = { zh: 5, en: 4 }

function textInlines(text: string, layout: 'zh' | 'en'): Inline[] {
  const out: Inline[] = []
  let last = 0
  for (const m of text.matchAll(INLINE_RE[layout])) {
    if (m.index! > last) out.push({ kind: 'text', text: text.slice(last, m.index) })
    if (m[1]) out.push({ kind: 'blank', width: 4, label: m[1] })
    else if (m[2]) out.push({ kind: 'blank', width: Math.max(BLANK_WIDTH[layout], Math.min(m[2].length * 0.5, 12)) })
    else out.push({ kind: 'ipa', text: m[0] })
    last = m.index! + m[0].length
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) })
  return out
}

// ---------- 分行 ----------

/** 英语对话：句末标点后的「—」另起一行，如 "—Hey, Nick. Is this yours? —No, it isn't." */
const TURN_BREAK_RE = /([.?!。？！'’"”)）])[ \t]*(?=[—―]{1,2}[-\s]*\S)/g

/** 按换行切成行，独立公式单独成行；每行为行内片段 */
function splitLines(text: string, layout: 'zh' | 'en'): { inlines: Inline[]; display: boolean }[] {
  const lines: { inlines: Inline[]; display: boolean }[] = [{ inlines: [], display: false }]
  const cur = () => lines[lines.length - 1]
  for (const seg of splitMath(text)) {
    if (seg.math && seg.display) {
      lines.push({ inlines: [{ kind: 'math', tex: seg.text, display: true }], display: true }, { inlines: [], display: false })
    } else if (seg.math) {
      cur().inlines.push({ kind: 'math', tex: seg.text, display: false })
    } else {
      const t = layout === 'en' ? seg.text.replace(TURN_BREAK_RE, '$1\n') : seg.text
      t.split('\n').forEach((part, i) => {
        if (i) lines.push({ inlines: [], display: false })
        if (part) cur().inlines.push(...textInlines(part, layout))
      })
    }
  }
  return lines.filter((l) => l.inlines.some((x) => x.kind !== 'text' || x.text.trim()))
}

// ---------- 行的角色 ----------

/** 小问、序号、选项开头：（1）(1) ① 1. A. */
const SUB_RE = /^(（\d{1,2}）|\(\d{1,2}\)|[①-⑳]|\d{1,2}[.．、](?!\d)|[A-H][.．、]\s?)/
/** 对话开头：—、Tom: 、A： */
const TURN_RE = /^([—―–]|[A-Z][A-Za-z]{0,11}\s?[:：]\s)/
/** 句末或句中标点结尾的不是标题 */
const PUNCT_END_RE = /[。！？：；，、,.!?:;…—)）"”'’]$/
const CJK_RE = /^[一-鿿]+$/
/** 原文以全角空格或多个空格缩进的行是段落 */
const INDENT_RE = /^[　 \t]{2,}|^　/

const lineText = (l: Inline[]) => l.map((x) => (x.kind === 'text' || x.kind === 'ipa' ? x.text : x.kind === 'math' ? x.tex : '__')).join('')

/** 古诗词句：两句以上、每句字数相同（4–7 字）的纯汉字 */
function isVerse(text: string): boolean {
  const clauses = text.split(/[，。？！；、]/).filter(Boolean)
  return clauses.length >= 2 && /[，。？！；]$/.test(text) && clauses.every((c) => CJK_RE.test(c) && c.length === clauses[0].length)
    && clauses[0].length >= 4 && clauses[0].length <= 7
}

/** 正文段落的最小长度（字） */
const PARA_MIN = { zh: 28, en: 34 }

export function parseRich(text: string, layout: 'zh' | 'en'): Line[] {
  const raw = splitLines(text, layout)
  const info = raw.map((l) => {
    const t = lineText(l.inlines)
    const trimmed = t.trim()
    return { ...l, text: trimmed, indented: INDENT_RE.test(t), width: textWidth(trimmed) }
  })
  const isLong = (i: number) => !!info[i] && !info[i].display && info[i].width >= PARA_MIN[layout] * 1.5
  const isBody = (i: number) => isLong(i) || (layout === 'zh' && !!info[i] && isVerse(info[i].text))
  const isShortHead = (t: string) => (layout === 'zh'
    ? [...t].length <= 20 && !PUNCT_END_RE.test(t)
    : t.split(/\s+/).length <= 10 && t.length <= 64 && /^[A-Z“"'‘]/.test(t) && !PUNCT_END_RE.test(t))

  const roles: LineRole[] = []
  info.forEach((l, i) => {
    const t = l.text
    let role: LineRole
    if (l.display) role = 'display'
    else if (layout === 'zh' && isVerse(t)) role = 'verse'
    else if (SUB_RE.test(t)) role = 'sub'
    else if (layout === 'en' && TURN_RE.test(t)) role = 'turn'
    // 材料标题：短行、不以标点结尾，下面接正文段落或诗句（中间可隔一行作者）
    else if (isShortHead(t) && (isBody(i + 1) || (isShortHead(info[i + 1]?.text ?? '') && isBody(i + 2)))) {
      role = roles[i - 1] === 'title' ? 'byline' : 'title'
    }
    // 作者、出处行：紧跟标题的短行，或（节选）之类
    else if (roles[i - 1] === 'title' && [...t].length <= 24 && !PUNCT_END_RE.test(t.replace(/[)）]$/, ''))) role = 'byline'
    else if (/^[（(][^（()）]{1,12}[)）]$/.test(t) && roles[i - 1] === 'verse') role = 'byline'
    else if (l.indented || l.width >= PARA_MIN[layout]) role = 'para'
    else role = 'plain'
    roles.push(role)
  })

  return info.map((l, i) => {
    const inlines = [...l.inlines]
    // 段首空白由排版缩进代替
    const first = inlines[0]
    if (first?.kind === 'text') inlines[0] = { kind: 'text', text: first.text.replace(/^[\s　]+/, '') }
    return { role: roles[i], inlines }
  })
}

// ---------- 渲染 HTML ----------

const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)

export const renderTex = (tex: string, display: boolean) =>
  katex.renderToString(tex, { throwOnError: false, strict: 'ignore', displayMode: display, output: 'html' })

function inlineHtml(x: Inline): string {
  switch (x.kind) {
    case 'text': return escapeHtml(x.text)
    case 'math': return renderTex(x.tex, x.display)
    case 'ipa': return `<span class="ipa">${escapeHtml(x.text)}</span>`
    case 'blank': return `<span class="blank${x.label ? ' no' : ''}" style="min-width:${x.width}em">${x.label ? escapeHtml(x.label) : ''}</span>`
  }
}

/** 首行若是正文、小问等，紧跟在题号后同一行显示（行内），标题、诗句、独立公式另起一行 */
const LEAD_INLINE: LineRole[] = ['para', 'plain', 'sub', 'turn']

/** block：独立成块的文本（阅读材料），首行不接在题号后 */
export function renderRich(text: string, layout: 'zh' | 'en', block = false): string {
  return parseRich(text, layout).map((l, i) => {
    const lead = !block && i === 0 && LEAD_INLINE.includes(l.role) ? ' lead' : ''
    return `<span class="ln ln-${l.role}${lead}">${l.inlines.map(inlineHtml).join('')}</span>`
  }).join('')
}
