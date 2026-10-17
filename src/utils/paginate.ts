/**
 * 试卷分页：按纸张大小把卷头、大题标题、题目等块依次装入各栏（A3 双栏每页两栏），装不下就换栏 / 换页。
 * 页边距、装订线与 Word 导出（utils/docx.ts）一致。
 */

export type PaperSize = 'A4' | 'A3 双栏' | 'B4'

/** 1 毫米对应的 CSS 像素（96 dpi） */
export const MM = 96 / 25.4

/** 纸张（毫米） */
const PAGE: Record<PaperSize, { w: number; h: number; cols: number }> = {
  'A4': { w: 210, h: 297, cols: 1 },
  'B4': { w: 257, h: 364, cols: 1 },
  'A3 双栏': { w: 420, h: 297, cols: 2 },
}
const MARGIN = 20
const GUTTER = 12
const COL_GAP = 15

export interface PageBox {
  /** 纸张宽高、左边距（含装订线位置）、栏宽、栏间距，单位毫米 */
  w: number
  h: number
  margin: number
  left: number
  cols: number
  colWidth: number
  colGap: number
  /** 每栏可排内容的高度（像素） */
  colHeight: number
}

export function pageBox(size: PaperSize, binding: boolean): PageBox {
  const p = PAGE[size]
  const left = MARGIN + (binding ? GUTTER : 0)
  const inner = p.w - left - MARGIN
  return {
    w: p.w, h: p.h, margin: MARGIN, left, cols: p.cols, colGap: COL_GAP,
    colWidth: (inner - COL_GAP * (p.cols - 1)) / p.cols,
    colHeight: (p.h - MARGIN * 2) * MM,
  }
}

export interface FlowBlock {
  key: string
  /** 与上一块的间距（像素）；位于栏首时不计 */
  gap: number
  /** 不单独留在栏末（大题标题、阅读材料），与下一块一起换栏 */
  keepWithNext?: boolean
}

/** 依次装栏，返回每栏的块 key；单块高于一栏时独占一栏 */
export function paginate(blocks: FlowBlock[], heights: number[], colHeight: number): string[][] {
  const cols: string[][] = [[]]
  let y = 0
  blocks.forEach((b, i) => {
    let col = cols[cols.length - 1]
    let need = (col.length ? b.gap : 0) + heights[i]
    if (b.keepWithNext && i + 1 < blocks.length) need += blocks[i + 1].gap + heights[i + 1]
    if (col.length && y + need > colHeight) {
      col = []
      cols.push(col)
      y = 0
    }
    y += (col.length ? b.gap : 0) + heights[i]
    col.push(b.key)
  })
  return cols
}
