import JSZip from 'jszip'
import { latexToOmml } from './omml'
import { splitMath } from './math'

/**
 * 生成试卷 .docx：公式为 Word 原生公式（可在 Word 中直接编辑），题目配图嵌入文档。
 * 手写最小的 WordprocessingML 包（document、styles、图片与关系），不依赖服务端。
 */

export interface DocxItem {
  no: number
  /** 题号后的分值标注，如「（12 分）」 */
  scoreMark?: string
  stem: string
  options: string[]
  images: string[]
  answer?: string | null
  analysis?: string | null
}

export interface DocxPaper {
  secret: string
  title: string
  subtitle: string
  info: string
  fields: string[]
  notice: string
  sections: { heading: string; items: DocxItem[] }[]
  showAnswer: boolean
  /** 答题卡题数，0 为不附答题卡 */
  answerCard: number
  size: 'A4' | 'A3 双栏' | 'B4'
  /** 装订线：左侧留出装订边距 */
  binding: boolean
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
const LETTERS = 'ABCDEFGH'

// ---------- 页面 ----------

/** 纸张（twips，1 cm ≈ 567） */
const PAGE = {
  'A4': { w: 11906, h: 16838, cols: 1, landscape: false },
  'B4': { w: 14570, h: 20636, cols: 1, landscape: false },
  'A3 双栏': { w: 23811, h: 16838, cols: 2, landscape: true },
} as const
const MARGIN = 1134
const GUTTER = 680
const COL_SPACE = 850

function textWidth(p: DocxPaper): number {
  const pg = PAGE[p.size]
  const w = pg.w - MARGIN * 2 - (p.binding ? GUTTER : 0)
  return pg.cols > 1 ? Math.floor((w - COL_SPACE) / pg.cols) : w
}

function sectPr(p: DocxPaper): string {
  const pg = PAGE[p.size]
  return `<w:sectPr><w:pgSz w:w="${pg.w}" w:h="${pg.h}"${pg.landscape ? ' w:orient="landscape"' : ''}/>`
    + `<w:pgMar w:top="${MARGIN}" w:right="${MARGIN}" w:bottom="${MARGIN}" w:left="${MARGIN}" w:header="567" w:footer="567" w:gutter="${p.binding ? GUTTER : 0}"/>`
    + `<w:cols w:num="${pg.cols}" w:space="${COL_SPACE}"${pg.cols > 1 ? ' w:sep="1"' : ''}/></w:sectPr>`
}

// ---------- 段落与文字 ----------

interface RunStyle { bold?: boolean; size?: number; color?: string; spacing?: number; font?: 'hei' }
interface ParaStyle {
  align?: 'center' | 'left'; before?: number; after?: number; indent?: number; keepNext?: boolean
  tabs?: number[]; border?: boolean
}

function rPr(s: RunStyle = {}): string {
  const parts = [
    s.font === 'hei' ? '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="黑体"/>' : '',
    s.bold ? '<w:b/><w:bCs/>' : '',
    s.color ? `<w:color w:val="${s.color}"/>` : '',
    s.spacing ? `<w:spacing w:val="${s.spacing}"/>` : '',
    s.size ? `<w:sz w:val="${s.size * 2}"/><w:szCs w:val="${s.size * 2}"/>` : '',
  ].join('')
  return parts ? `<w:rPr>${parts}</w:rPr>` : ''
}

function textRuns(text: string, s: RunStyle = {}): string {
  // 换行 → w:br，制表符 → w:tab
  return text.split('\n').map((line, i) => (i ? `<w:r>${rPr(s)}<w:br/></w:r>` : '')
    + line.split('\t').map((seg, j) => (j ? `<w:r>${rPr(s)}<w:tab/></w:r>` : '')
      + (seg ? `<w:r>${rPr(s)}<w:t xml:space="preserve">${esc(seg)}</w:t></w:r>` : '')).join('')).join('')
}

/** 夹带公式的文字：公式转为原生公式，KaTeX 无法解析的保留原文 */
function richRuns(text: string, s: RunStyle = {}): string {
  return splitMath(text).map((seg) => {
    if (!seg.math) return textRuns(seg.text, s)
    return latexToOmml(seg.text, seg.display) ?? textRuns(seg.display ? `$$${seg.text}$$` : `$${seg.text}$`, s)
  }).join('')
}

function para(content: string, s: ParaStyle = {}): string {
  const pPr = [
    s.keepNext ? '<w:keepNext/>' : '',
    s.border ? '<w:pBdr><w:top w:val="single" w:sz="4" w:space="4" w:color="999999"/><w:left w:val="single" w:sz="4" w:space="4" w:color="999999"/><w:bottom w:val="single" w:sz="4" w:space="4" w:color="999999"/><w:right w:val="single" w:sz="4" w:space="4" w:color="999999"/></w:pBdr>' : '',
    s.tabs?.length ? `<w:tabs>${s.tabs.map((t) => `<w:tab w:val="left" w:pos="${t}"/>`).join('')}</w:tabs>` : '',
    s.before || s.after ? `<w:spacing w:before="${s.before ?? 0}" w:after="${s.after ?? 0}"/>` : '',
    s.indent ? `<w:ind w:left="${s.indent}"/>` : '',
    s.align ? `<w:jc w:val="${s.align === 'center' ? 'center' : 'left'}"/>` : '',
  ].join('')
  return `<w:p>${pPr ? `<w:pPr>${pPr}</w:pPr>` : ''}${content}</w:p>`
}

// ---------- 选项排版 ----------

/** 选项的大致宽度（字），公式按 LaTeX 长度折半估算 */
function optionWidth(o: string): number {
  return splitMath(o).reduce((a, s) => a + (s.math ? Math.ceil(s.text.length / 2) : [...s.text].length), 0) + 3
}

function optionParas(options: string[], width: number): string {
  const INDENT = 420
  const chars = (width - INDENT) / 210 // 五号字约 210 twips
  const max = Math.max(...options.map(optionWidth))
  const perLine = max <= chars / 4 ? 4 : max <= chars / 2 ? 2 : 1
  const col = Math.floor((width - INDENT) / perLine)
  const tabs = Array.from({ length: perLine - 1 }, (_, i) => INDENT + col * (i + 1))
  const out: string[] = []
  for (let i = 0; i < options.length; i += perLine) {
    const row = options.slice(i, i + perLine).map((o, j) => (j ? '<w:r><w:tab/></w:r>' : '') + textRuns(`${LETTERS[i + j]}．`) + richRuns(o))
    out.push(para(row.join(''), { indent: INDENT, tabs }))
  }
  return out.join('')
}

// ---------- 图片 ----------

interface Media { name: string; data: ArrayBuffer; cx: number; cy: number; rid: string }

const EMU_PER_PX = 9525
const MAX_IMAGE_EMU = 7 * 360000 // 最宽 7 cm

async function loadImage(url: string, index: number): Promise<Media | null> {
  try {
    // 配图地址为本服务的 /api/files（同源，自动携带登录 Cookie）；使用 OSS / COS 时会重定向到签名地址，
    // 跨域跳转后不能再带 Cookie，否则需要对象存储允许凭据跨域
    const res = await fetch(url, { credentials: 'same-origin' })
    if (!res.ok) return null
    let blob = await res.blob()
    const bmp = await createImageBitmap(blob)
    let ext = ({ 'image/png': 'png', 'image/jpeg': 'jpeg', 'image/gif': 'gif' } as Record<string, string>)[blob.type]
    if (!ext) { // 其他格式（webp 等）转为 PNG
      const canvas = document.createElement('canvas')
      canvas.width = bmp.width
      canvas.height = bmp.height
      canvas.getContext('2d')!.drawImage(bmp, 0, 0)
      blob = await new Promise<Blob>((r, j) => canvas.toBlob((b) => (b ? r(b) : j(new Error('图片转换失败'))), 'image/png'))
      ext = 'png'
    }
    let cx = bmp.width * EMU_PER_PX, cy = bmp.height * EMU_PER_PX
    if (cx > MAX_IMAGE_EMU) { cy = Math.round(cy * MAX_IMAGE_EMU / cx); cx = MAX_IMAGE_EMU }
    return { name: `image${index}.${ext}`, data: await blob.arrayBuffer(), cx, cy, rid: `rIdImg${index}` }
  } catch {
    return null
  }
}

function imageRun(m: Media, id: number): string {
  return `<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="${m.cx}" cy="${m.cy}"/>`
    + `<wp:docPr id="${id}" name="图片 ${id}"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">`
    + `<pic:pic><pic:nvPicPr><pic:cNvPr id="${id}" name="${m.name}"/><pic:cNvPicPr/></pic:nvPicPr>`
    + `<pic:blipFill><a:blip r:embed="${m.rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>`
    + `<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${m.cx}" cy="${m.cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>`
    + '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
}

// ---------- 答题卡 ----------

function answerCard(n: number, width: number): string {
  const COLS = 5
  const w = Math.floor(width / COLS)
  const border = '<w:tblBorders>' + ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']
    .map((b) => `<w:${b} w:val="single" w:sz="4" w:space="0" w:color="999999"/>`).join('') + '</w:tblBorders>'
  const rows: string[] = []
  for (let i = 0; i < n; i += COLS) {
    const cells = Array.from({ length: COLS }, (_, j) => {
      const no = i + j + 1
      return `<w:tc><w:tcPr><w:tcW w:w="${w}" w:type="dxa"/></w:tcPr>${para(no <= n ? textRuns(`${no}．`) : '', { before: 60, after: 60 })}</w:tc>`
    })
    rows.push(`<w:tr><w:trPr><w:trHeight w:val="600"/></w:trPr>${cells.join('')}</w:tr>`)
  }
  return `<w:tbl><w:tblPr><w:tblW w:w="${w * COLS}" w:type="dxa"/>${border}<w:tblLayout w:type="fixed"/></w:tblPr>`
    + `<w:tblGrid>${Array.from({ length: COLS }, () => `<w:gridCol w:w="${w}"/>`).join('')}</w:tblGrid>${rows.join('')}</w:tbl>`
}

// ---------- 组装 ----------

const NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
  + 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
  + 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
  + 'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
  + 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
  + 'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'

const XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

const STYLES = `${XML}<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults>`
  + '<w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体" w:cs="Times New Roman"/>'
  + '<w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault>'
  + '<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
  + '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style></w:styles>'

const SETTINGS = `${XML}<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" `
  + 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
  + '<m:mathPr><m:mathFont m:val="Cambria Math"/><m:dispDef/><m:lMargin m:val="0"/><m:rMargin m:val="0"/>'
  + '<m:defJc m:val="centerGroup"/><m:intLim m:val="subSup"/><m:naryLim m:val="undOvr"/></m:mathPr></w:settings>'

export async function buildDocx(p: DocxPaper): Promise<Blob> {
  const width = textWidth(p)
  const body: string[] = []

  // 卷头
  body.push(para(textRuns(p.secret, { size: 9, spacing: 40 }), { align: 'center' }))
  body.push(para(textRuns(p.title, { bold: true, size: 15, font: 'hei' }), { align: 'center', before: 120, after: 60 }))
  body.push(para(textRuns(p.subtitle, { bold: true, size: 14, spacing: 60, font: 'hei' }), { align: 'center', after: 60 }))
  body.push(para(textRuns(p.info), { align: 'center' }))
  body.push(para(textRuns(p.fields.join('　　')), { align: 'center', after: 120 }))
  body.push(para(textRuns(p.notice, { size: 9 }), { border: true, after: 200 }))

  // 题目配图并行下载
  const urls = [...new Set(p.sections.flatMap((s) => s.items.flatMap((q) => q.images)))]
  const media = new Map<string, Media>()
  await Promise.all(urls.map(async (u, i) => {
    const m = await loadImage(u, i + 1)
    if (m) media.set(u, m)
  }))
  let drawingId = 1

  for (const s of p.sections) {
    body.push(para(textRuns(s.heading, { bold: true, font: 'hei' }), { before: 200, after: 80, keepNext: true }))
    for (const q of s.items) {
      body.push(para(textRuns(`${q.no}．${q.scoreMark ?? ''}`) + richRuns(q.stem), { before: 60 }))
      const imgs = q.images.map((u) => media.get(u)).filter((m): m is Media => !!m)
      if (imgs.length) body.push(para(imgs.map((m) => imageRun(m, drawingId++)).join(textRuns('　')), { indent: 420 }))
      if (q.options.length) body.push(optionParas(q.options, width))
      if (p.showAnswer) {
        const style = { size: 9, color: '1F4E79' }
        const ans = textRuns('【答案】', { ...style, bold: true }) + richRuns(q.answer || '略', style)
          + (q.analysis ? textRuns('　【解析】', { ...style, bold: true }) + richRuns(q.analysis, style) : '')
        body.push(para(ans, { indent: 420, after: 60 }))
      }
    }
  }

  if (p.answerCard > 0) {
    body.push(para(textRuns('答题卡', { bold: true, font: 'hei' }), { before: 360, after: 120, keepNext: true }))
    body.push(answerCard(p.answerCard, width))
    body.push(para(''))
  }

  const document = `${XML}<w:document ${NS}><w:body>${body.join('')}${sectPr(p)}</w:body></w:document>`
  const images = [...media.values()]
  const zip = new JSZip()
  // 不写入目录条目（部分校验工具要求 OPC 包只含文件）
  const add = (name: string, data: string | ArrayBuffer) => zip.file(name, data, { createFolders: false })
  add('[Content_Types].xml', `${XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">`
    + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    + '<Default Extension="xml" ContentType="application/xml"/>'
    + '<Default Extension="png" ContentType="image/png"/><Default Extension="jpeg" ContentType="image/jpeg"/>'
    + '<Default Extension="gif" ContentType="image/gif"/>'
    + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    + '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    + '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
    + '</Types>')
  add('_rels/.rels', `${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`
    + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    + '</Relationships>')
  add('word/_rels/document.xml.rels', `${XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`
    + '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    + '<Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
    + images.map((m) => `<Relationship Id="${m.rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/${m.name}"/>`).join('')
    + '</Relationships>')
  add('word/document.xml', document)
  add('word/styles.xml', STYLES)
  add('word/settings.xml', SETTINGS)
  for (const m of images) add(`word/media/${m.name}`, m.data)
  return zip.generateAsync({ type: 'blob', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
}
