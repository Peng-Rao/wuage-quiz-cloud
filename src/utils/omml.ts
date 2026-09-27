import katex from 'katex'

/**
 * LaTeX → Word 公式（OMML），用于导出 .docx 时生成可编辑的原生公式。
 * 先用 KaTeX 转为 MathML，再把 MathML 元素逐一映射为 OMML：分式、根式、上下标、求和积分、极限、
 * 括号（\left \right）、分段函数（cases）、矩阵、向量与上划线等；KaTeX 不认识的写法保留为原文。
 */

const M_NS = 'http://www.w3.org/1998/Math/MathML'
/** 大型运算符：转为 m:nary，其后的一项作为被积 / 被求和式 */
const NARY = new Set(['∑', '∏', '∐', '∫', '∬', '∭', '∮', '⋃', '⋂', '⋁', '⋀'])
/** 下方带条件的函数名：lim x→0 等转为 m:limLow */
const LIMIT_FUNCS = new Set(['lim', 'max', 'min', 'sup', 'inf', 'limsup', 'liminf'])
/** 不可见字符：函数应用、不可见乘号等 */
const INVISIBLE = /[⁡⁢⁣⁤]/g

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
const attr = (s: string) => esc(s)

const MATH_FONT = '<w:rPr><w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/></w:rPr>'
const CJK_FONT = '<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体"/></w:rPr>'

/** m:r；style 为 m:rPr 内容（如正体、双线体） */
function run(text: string, style = '', cjk = false): string {
  const t = text.replace(INVISIBLE, '')
  if (!t) return ''
  const rPr = style ? `<m:rPr>${style}</m:rPr>` : ''
  return `<m:r>${rPr}${cjk ? CJK_FONT : MATH_FONT}<m:t xml:space="preserve">${esc(t)}</m:t></m:r>`
}

const VARIANT: Record<string, string> = {
  'normal': '<m:sty m:val="p"/>',
  'bold': '<m:sty m:val="b"/>',
  'bold-italic': '<m:sty m:val="bi"/>',
  'double-struck': '<m:scr m:val="double-struck"/><m:sty m:val="p"/>',
  'script': '<m:scr m:val="script"/>',
  'fraktur': '<m:scr m:val="fraktur"/>',
  'sans-serif': '<m:scr m:val="sans-serif"/><m:sty m:val="p"/>',
  'monospace': '<m:scr m:val="monospace"/><m:sty m:val="p"/>',
}

const kids = (el: Element) => [...el.children]
const text = (el: Element) => (el.textContent ?? '').replace(INVISIBLE, '')
const isMo = (el: Element | undefined, chars?: Set<string> | string[]) =>
  !!el && el.localName === 'mo' && (!chars || [...chars].includes(text(el).trim()))

/** 一组元素：处理大型运算符吸收其后一项、\left \right 括号 */
function seq(els: Element[]): string {
  let out = ''
  for (let i = 0; i < els.length; i++) {
    const el = els[i]
    const op = naryOp(el)
    if (op) {
      // 被积 / 被求和式：其后的一项（没有时为空）
      const next = els[i + 1]
      out += nary(el, op, next ? node(next) : '')
      if (next) i++
      continue
    }
    out += node(el)
  }
  return out
}

/** 元素本身或其底数是大型运算符时返回运算符字符 */
function naryOp(el: Element): string | null {
  if (isMo(el, NARY)) return text(el).trim()
  if (['msub', 'msup', 'msubsup', 'munder', 'mover', 'munderover'].includes(el.localName) && isMo(kids(el)[0], NARY)) {
    return text(kids(el)[0]).trim()
  }
  return null
}

function nary(el: Element, op: string, body: string): string {
  const k = kids(el)
  let sub = '', sup = ''
  const under = el.localName.startsWith('mu') || el.localName === 'mover'
  if (el.localName === 'msub' || el.localName === 'munder') sub = node(k[1])
  else if (el.localName === 'msup' || el.localName === 'mover') sup = node(k[1])
  else if (el.localName === 'msubsup' || el.localName === 'munderover') { sub = node(k[1]); sup = node(k[2]) }
  const pr = `<m:naryPr><m:chr m:val="${attr(op)}"/><m:limLoc m:val="${under ? 'undOvr' : 'subSup'}"/>`
    + `${sub ? '' : '<m:subHide m:val="1"/>'}${sup ? '' : '<m:supHide m:val="1"/>'}</m:naryPr>`
  return `<m:nary>${pr}<m:sub>${sub}</m:sub><m:sup>${sup}</m:sup><m:e>${body}</m:e></m:nary>`
}

/** 括号：\left( ... \right) 对应 fence="true" 的 mo */
function fenced(el: Element): string | null {
  const k = kids(el)
  if (k.length < 2) return null
  const first = k[0], last = k[k.length - 1]
  if (!isMo(first) || first.getAttribute('fence') !== 'true') return null
  const hasClose = isMo(last) && last.getAttribute('fence') === 'true' && last !== first
  const inner = k.slice(1, hasClose ? -1 : undefined)
  const beg = text(first).trim(), end = hasClose ? text(last).trim() : ''
  // 分段函数：{ + 表格 → 方程组
  if (beg === '{' && !end && inner.length === 1 && inner[0].localName === 'mtable') {
    const rows = kids(inner[0]).map((tr) => `<m:e>${kids(tr).map((td) => seq(kids(td))).join(run('  ', '<m:sty m:val="p"/>'))}</m:e>`)
    return `<m:d><m:dPr><m:begChr m:val="{"/><m:endChr m:val=""/></m:dPr><m:e><m:eqArr>${rows.join('')}</m:eqArr></m:e></m:d>`
  }
  return `<m:d><m:dPr><m:begChr m:val="${attr(beg)}"/><m:endChr m:val="${attr(end)}"/></m:dPr><m:e>${seq(inner)}</m:e></m:d>`
}

/** 上方的记号：向量箭头、帽子等为 m:acc，上划线为 m:bar，其他为 m:limUpp */
function over(base: Element, mark: Element): string {
  const ch = text(mark).trim()
  if (['‾', '¯', '_', '―', '─'].includes(ch)) return `<m:bar><m:barPr><m:pos m:val="top"/></m:barPr><m:e>${node(base)}</m:e></m:bar>`
  if (mark.localName === 'mo' && ch.length === 1) {
    const acc = ch === '→' ? '⃗' : ch
    return `<m:acc><m:accPr><m:chr m:val="${attr(acc)}"/></m:accPr><m:e>${node(base)}</m:e></m:acc>`
  }
  return `<m:limUpp><m:e>${node(base)}</m:e><m:lim>${node(mark)}</m:lim></m:limUpp>`
}

function under(base: Element, mark: Element): string {
  const ch = text(mark).trim()
  if (['_', '‾', '¯', '―', '─'].includes(ch)) return `<m:bar><m:barPr><m:pos m:val="bot"/></m:barPr><m:e>${node(base)}</m:e></m:bar>`
  return `<m:limLow><m:e>${node(base)}</m:e><m:lim>${node(mark)}</m:lim></m:limLow>`
}

function node(el: Element | undefined): string {
  if (!el) return ''
  const k = kids(el)
  switch (el.localName) {
    case 'math': case 'mrow': case 'mstyle': case 'mpadded': case 'menclose':
      return el.localName === 'mrow' ? (fenced(el) ?? seq(k)) : seq(k)
    case 'semantics':
      return node(k[0])
    case 'annotation': case 'annotation-xml': case 'mphantom':
      return ''
    case 'mi': {
      const t = text(el)
      const v = el.getAttribute('mathvariant') ?? (t.length > 1 ? 'normal' : '')
      return run(t, VARIANT[v] ?? '')
    }
    case 'mn':
      return run(text(el), VARIANT[el.getAttribute('mathvariant') ?? ''] ?? '')
    case 'mo':
      return run(text(el), '<m:sty m:val="p"/>')
    case 'mtext': {
      const t = text(el)
      return run(t, '<m:nor/>', /[　-鿿＀-￯]/.test(t))
    }
    case 'mspace': {
      const w = parseFloat(el.getAttribute('width') ?? '0')
      return w >= 0.5 ? run(' ', '<m:sty m:val="p"/>') : ''
    }
    case 'msup': {
      if (naryOp(el)) return nary(el, naryOp(el)!, '')
      return `<m:sSup><m:e>${node(k[0])}</m:e><m:sup>${node(k[1])}</m:sup></m:sSup>`
    }
    case 'msub': {
      if (naryOp(el)) return nary(el, naryOp(el)!, '')
      const base = text(k[0]).trim()
      if (LIMIT_FUNCS.has(base)) {
        return `<m:limLow><m:e>${run(base, '<m:sty m:val="p"/>')}</m:e><m:lim>${node(k[1])}</m:lim></m:limLow>`
      }
      return `<m:sSub><m:e>${node(k[0])}</m:e><m:sub>${node(k[1])}</m:sub></m:sSub>`
    }
    case 'msubsup':
      if (naryOp(el)) return nary(el, naryOp(el)!, '')
      return `<m:sSubSup><m:e>${node(k[0])}</m:e><m:sub>${node(k[1])}</m:sub><m:sup>${node(k[2])}</m:sup></m:sSubSup>`
    case 'mfrac': {
      const noBar = /^0(\.0+)?(px|em|pt)?$/.test(el.getAttribute('linethickness') ?? '')
      return `<m:f>${noBar ? '<m:fPr><m:type m:val="noBar"/></m:fPr>' : ''}<m:num>${node(k[0])}</m:num><m:den>${node(k[1])}</m:den></m:f>`
    }
    case 'msqrt':
      return `<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/><m:e>${seq(k)}</m:e></m:rad>`
    case 'mroot':
      return `<m:rad><m:deg>${node(k[1])}</m:deg><m:e>${node(k[0])}</m:e></m:rad>`
    case 'mover':
      if (naryOp(el)) return nary(el, naryOp(el)!, '')
      return over(k[0], k[1])
    case 'munder': {
      if (naryOp(el)) return nary(el, naryOp(el)!, '')
      const base = text(k[0]).trim()
      if (LIMIT_FUNCS.has(base)) {
        return `<m:limLow><m:e>${run(base, '<m:sty m:val="p"/>')}</m:e><m:lim>${node(k[1])}</m:lim></m:limLow>`
      }
      return under(k[0], k[1])
    }
    case 'munderover':
      if (naryOp(el)) return nary(el, naryOp(el)!, '')
      return `<m:limUpp><m:e><m:limLow><m:e>${node(k[0])}</m:e><m:lim>${node(k[1])}</m:lim></m:limLow></m:e><m:lim>${node(k[2])}</m:lim></m:limUpp>`
    case 'mtable': {
      const rows = k.map((tr) => `<m:mr>${kids(tr).map((td) => `<m:e>${seq(kids(td))}</m:e>`).join('')}</m:mr>`)
      return `<m:m>${rows.join('')}</m:m>`
    }
    default:
      return seq(k)
  }
}

let parser: DOMParser | null = null

/** LaTeX 转为 OMML：行内为 m:oMath，独立公式为 m:oMathPara；KaTeX 无法解析时返回 null（调用方按原文输出） */
export function latexToOmml(tex: string, display = false): string | null {
  let mathml: string
  try {
    mathml = katex.renderToString(tex, { output: 'mathml', throwOnError: true, displayMode: display })
  } catch {
    return null
  }
  parser ??= new DOMParser()
  // HTML 解析器对实体等更宽容，<math> 内的元素仍在 MathML 命名空间中
  const doc = parser.parseFromString(mathml, 'text/html')
  const math = doc.getElementsByTagNameNS(M_NS, 'math')[0]
  if (!math) return null
  const body = `<m:oMath>${node(math)}</m:oMath>`
  return display ? `<m:oMathPara>${body}</m:oMathPara>` : body
}
