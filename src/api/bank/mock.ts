import { DIFF_COEFS, QUESTIONS, coefToDiff } from '@/data/mock'
import { mockParseApi } from '../parse/mock'
import type { KnowledgePointRef, KnowledgeTreeNode, PaperMeta, QuestionType } from '../parse/types'
import type { BankApi, BankQuestion, FacetCount, PaperDetail, PaperSummary, TextbookVersion } from './types'

// 与后端共用内置教材目录（server/app/data/chapters）
type RawChapters = {
  stage: string; subject: string; version: string; order?: number; region?: string
  books: { name: string; grade: string; edition?: string; chapters: { name: string; knowledge?: string[]; sections: { name: string; knowledge: string[] }[] }[] }[]
}
const rawChapters = import.meta.glob<RawChapters>('../../../server/app/data/chapters/*.json', { eager: true, import: 'default' })
/** 章或节 id → 对应的知识点名称（章为本身与各节之和） */
const CHAPTER_KNOWLEDGE = new Map<string, Set<string>>()
/** mock 中章节 id 直接用名称路径；同一学科多个版本时 order 小的为默认 */
const CATALOG = Object.values(rawChapters).sort((a, b) => (a.order ?? 0) - (b.order ?? 0)).map((v) => ({
  stage: v.stage, subject: v.subject,
  version: {
    name: v.version, region: v.region ?? '',
    books: v.books.map((b) => {
      const bid = `${v.stage}${v.subject}/${v.version}/${b.name}`
      return {
        id: bid, name: b.name, grade: b.grade, edition: b.edition ?? '',
        chapters: b.chapters.map((c, ci) => {
          // 同一级重名时用序号区分（与后端保证 id 唯一的方式不同，mock 只需唯一）
          const cid = `${bid}/${ci}.${c.name}`
          const sections = c.sections.map((x, si) => ({ id: `${cid}/${si}.${x.name}`, name: x.name, knowledge: x.knowledge }))
          for (const x of sections) CHAPTER_KNOWLEDGE.set(x.id, new Set(x.knowledge))
          CHAPTER_KNOWLEDGE.set(cid, new Set([...(c.knowledge ?? []), ...c.sections.flatMap((x) => x.knowledge)]))
          return { id: cid, name: c.name, sections }
        }),
      }
    }),
  } as TextbookVersion,
}))
/** 知识点及其各级上级的名称 */
const kpNames = (k: KnowledgePointRef) => new Set([k.name, ...(k.path?.split(' / ') ?? [])])
const inChapter = (q: BankQuestion, names: Set<string>) => q.knowledgePoints.some((k) => [...kpNames(k)].some((n) => names.has(n)))
const province = (r: string) => r.split(/\s*[·•/]\s*/)[0]
const termOf = (m: PaperMeta) => (`${m.schoolYear} ${m.title}`.match(/\d{4}\s*(上|下)|(上|下)(?:学期|册)/) ?? []).slice(1).find(Boolean) ?? ''

/** 演示用校本题库：三份已入库的试卷，知识点按内置知识树路径标注 */

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v))

const R1 = '必修：函数、几何、概率统计基础'
const R2 = '选择性必修：解析、变化与推断'
const kp = (path: string): KnowledgePointRef => ({ id: 'kp:' + path, name: path.split(' / ').at(-1)!, path, inTree: true })

/** 示例题（data/mock QUESTIONS）对应的知识点 */
const KPS: Record<number, string[]> = {
  1: [`${R1} / 集合与逻辑 / 子集`],
  2: [`${R1} / 幂、指数、对数函数 / 对数函数的图像与性质`, `${R1} / 函数概念与性质 / 函数的图像`],
  3: [`${R2} / 导数与变化率 / 导数研究极值`, `${R1} / 函数概念与性质 / 函数的奇偶性`],
  4: [`${R1} / 平面向量与解三角形 / 向量垂直`],
  5: [`${R1} / 三角函数 / 和差角公式`],
  6: [`${R2} / 数列 / 等比数列的通项公式`, `${R2} / 数列 / 分组求和`],
}

type Seed = { id: string; meta: PaperMeta; date: string; items: Omit<BankQuestion, 'paperId' | 'source' | 'createdAt'>[] }

const fromSample = (id: number, score: number): Seed['items'][number] => {
  const q = QUESTIONS.find((x) => x.id === id)!
  return {
    id: 'b' + id, type: q.type, score, stem: q.stem, options: q.options.map((o) => o.replace(/^[A-H]．/, '')),
    answer: q.answer, analysis: q.analysis, answerSource: 'paper', knowledgePoints: KPS[id].map(kp),
    coef: DIFF_COEFS[['容易', '适中', '较难'].indexOf(q.diff)], images: [],
  }
}

const SEEDS: Seed[] = [
  {
    id: 'demo-haidian',
    meta: { title: '北京市海淀区 2026—2027 学年高一上学期期中数学试题', stage: '高中', subject: '数学', grade: '高一', paperType: '期中考试', region: '北京 · 海淀', schoolYear: '2026—2027', textbook: '人教A版' },
    date: '2026-09-24T09:30:00Z',
    items: [fromSample(1, 5), fromSample(5, 5), fromSample(4, 5), fromSample(2, 5)],
  },
  {
    id: 'demo-gaokao',
    meta: { title: '2026 年普通高等学校招生全国统一考试（新高考Ⅰ卷）数学', stage: '高中', subject: '数学', grade: '高三', paperType: '高考真题', region: '全国', schoolYear: '2025—2026', textbook: '' },
    date: '2026-06-10T08:00:00Z',
    items: [fromSample(3, 6), fromSample(6, 12)],
  },
  {
    id: 'demo-chem',
    meta: { title: '厦门市 2025—2026 学年九年级上学期化学 10 月月考', stage: '初中', subject: '化学', grade: '九年级', paperType: '月考', region: '福建 · 厦门', schoolYear: '2025—2026', textbook: '人教版' },
    date: '2025-10-20T10:00:00Z',
    items: [
      { id: 'c1', type: '单选题', score: 2, stem: '下列变化中，属于化学变化的是（　　）', options: ['冰雪融化', '酒精挥发', '铁锅生锈', '玻璃破碎'], answer: 'C', analysis: '铁锅生锈生成了新物质铁锈，属于化学变化。', answerSource: 'paper', knowledgePoints: [{ id: 'kp:c1', name: '物理变化与化学变化', path: null, inTree: false }], coef: 0.12, images: [] },
      { id: 'c2', type: '填空题', score: 4, stem: '用化学用语填空：2 个氢原子 ______；3 个水分子 ______。', options: [], answer: '2H；3H₂O', analysis: null, answerSource: 'paper', knowledgePoints: [{ id: 'kp:c2', name: '符号前系数与右下角下标', path: null, inTree: false }], coef: 0.35, images: [] },
    ],
  },
]

const QS: BankQuestion[] = SEEDS.flatMap((p) => p.items.map((q, i) => ({
  ...q, paperId: p.id, createdAt: p.date,
  source: {
    title: p.meta.title, fileName: p.meta.title + '.pdf', schoolYear: p.meta.schoolYear, region: p.meta.region,
    grade: p.meta.grade, paperType: p.meta.paperType, subject: p.meta.subject, no: i + 1, page: 1,
    label: `${p.meta.schoolYear} · ${p.meta.region} · ${p.meta.grade}${p.meta.paperType}《${p.meta.title}》第 ${i + 1} 题`,
  },
})))

const metaOf = (q: BankQuestion) => SEEDS.find((s) => s.id === q.paperId)!.meta

function summary(p: Seed): PaperSummary {
  const qs = QS.filter((q) => q.paperId === p.id)
  const total = qs.reduce((a, q) => a + q.score, 0)
  const typeCounts: Partial<Record<QuestionType, number>> = {}
  for (const q of qs) typeCounts[q.type] = (typeCounts[q.type] ?? 0) + 1
  return {
    id: p.id, title: p.meta.title, meta: p.meta, fileName: p.meta.title + '.pdf', questionCount: qs.length,
    sourceQuestionCount: qs.length, totalScore: total, typeCounts,
    avgCoef: total ? Math.round((qs.reduce((a, q) => a + q.coef * q.score, 0) / total) * 100) / 100 : null, updatedAt: p.date,
  }
}

/** 知识树各节点 id → 完整路径 */
async function treeIndex(treeId: string) {
  const tree = await mockParseApi.getTree(treeId)
  const path = new Map<string, string>()
  const walk = (nodes: KnowledgeTreeNode[], prefix: string) => nodes.forEach((n) => {
    const p = prefix ? `${prefix} / ${n.name}` : n.name
    path.set(n.id, p)
    walk(n.children, p)
  })
  walk(tree.nodes, '')
  return { tree, path }
}

const years = (t: string) => (t.match(/(?<!\d)(?:19|20)\d{2}(?!\d)/g) ?? []).map(Number)

function facet(papers: PaperSummary[], key: keyof PaperMeta): FacetCount[] {
  const c = new Map<string, number>()
  for (const p of papers) c.set(p.meta[key] || '未分类', (c.get(p.meta[key] || '未分类') ?? 0) + 1)
  return [...c].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count)
}

export const mockBankApi: BankApi = {
  async listQuestions(p) {
    await sleep(120)
    let paths: string[] | null = null
    if (p.nodeIds?.length && p.treeId) {
      const { path } = await treeIndex(p.treeId)
      paths = p.nodeIds.map((id) => path.get(id)).filter((x): x is string => !!x)
    }
    const chapter = p.chapterId ? CHAPTER_KNOWLEDGE.get(p.chapterId) ?? new Set<string>() : null
    const kw = p.q?.trim()
    let out = QS.filter((q) => {
      const m = metaOf(q)
      return (!p.stage || m.stage === p.stage) && (!p.subject || m.subject === p.subject)
        && (!p.paperId || q.paperId === p.paperId) && (!p.type || q.type === p.type)
        && (!p.diff || coefToDiff(q.coef) === p.diff)
        && (!p.paperTypes?.length || p.paperTypes.some((t) => (m.paperType + m.title).includes(t)))
        && (!p.region || province(m.region) === p.region) && (!p.grade || m.grade === p.grade)
        && (!p.term || termOf(m) === p.term) && (!chapter || inChapter(q, chapter))
        && (!p.year || (p.year.startsWith('<')
          ? years(m.schoolYear + m.title).some((y) => y < +p.year!.slice(1))
          : years(m.schoolYear + m.title).includes(+p.year)))
        && (!paths || q.knowledgePoints.some((k) => paths!.some((b) => k.path === b || k.path?.startsWith(b + ' / '))))
        && (!kw || [q.stem, ...q.options, ...q.knowledgePoints.map((k) => k.name), m.title].join(' ').includes(kw))
    })
    if (p.sort === 'latest') out = [...out].sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    if (p.sort === 'easy') out = [...out].sort((a, b) => a.coef - b.coef)
    if (p.sort === 'hard') out = [...out].sort((a, b) => b.coef - a.coef)
    const offset = p.offset ?? 0
    return clone({ items: out.slice(offset, offset + (p.limit ?? 10)), total: out.length })
  },

  async questionFacets(stage, subject) {
    await sleep(60)
    const metas = QS.map(metaOf).filter((m) => m.stage === stage && m.subject === subject)
    const count = (vals: string[]) => [...vals.reduce((c, v) => (v ? c.set(v, (c.get(v) ?? 0) + 1) : c), new Map<string, number>())]
      .map(([name, n]) => ({ name, count: n })).sort((a, b) => b.count - a.count)
    return {
      regions: count(metas.map((m) => province(m.region))),
      grades: count(metas.map((m) => m.grade)),
      years: count(metas.map((m) => String(Math.min(...years(m.schoolYear + m.title))))).sort((a, b) => b.name.localeCompare(a.name)),
    }
  },

  async chapters(stage, subject) {
    await sleep(60)
    return clone(CATALOG.filter((c) => c.stage === stage && c.subject === subject).map((c) => c.version))
  },

  async chapterCounts(bookId) {
    await sleep(60)
    const entry = CATALOG.find((c) => c.version.books.some((b) => b.id === bookId))
    if (!entry) throw new Error('教材不存在')
    const qs = QS.filter((q) => metaOf(q).stage === entry.stage && metaOf(q).subject === entry.subject)
    const counts: Record<string, number> = {}
    for (const [id, names] of CHAPTER_KNOWLEDGE) {
      if (!id.startsWith(bookId + '/')) continue
      const n = qs.filter((q) => inChapter(q, names)).length
      if (n) counts[id] = n
    }
    return counts
  },

  async knowledgeCounts(treeId) {
    await sleep(60)
    const { tree, path } = await treeIndex(treeId)
    const counts: Record<string, number> = {}
    for (const q of QS.filter((x) => metaOf(x).subject === tree.subject && metaOf(x).stage === tree.stage)) {
      // 节点路径是某个知识点路径本身或其前缀时计入（每题每节点只计一次）
      for (const [id, p] of path) {
        if (q.knowledgePoints.some((k) => k.path === p || k.path?.startsWith(p + ' / '))) counts[id] = (counts[id] ?? 0) + 1
      }
    }
    return counts
  },

  async listPapers(p) {
    await sleep(120)
    const all = SEEDS.map(summary).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    const match = (x: PaperSummary, skip?: keyof PaperMeta) =>
      (!p.stage || skip === 'stage' || (x.meta.stage || '未分类') === p.stage)
      && (!p.grade || skip === 'grade' || (x.meta.grade || '未分类') === p.grade)
      && (!p.subject || skip === 'subject' || (x.meta.subject || '未分类') === p.subject)
      && (!p.paperType || skip === 'paperType' || (x.meta.paperType || '未分类') === p.paperType)
      && (!p.textbook || skip === 'textbook' || (x.meta.textbook || '未分类') === p.textbook)
      && (!p.category?.length || p.category.some((k) => (x.meta.paperType + x.title).includes(k)))
      && (!p.q || [x.title, x.meta.region, x.meta.schoolYear].join(' ').includes(p.q))
    const items = all.filter((x) => match(x))
    const offset = p.offset ?? 0
    return clone({
      items: items.slice(offset, offset + (p.limit ?? 20)), total: items.length,
      facets: {
        stages: facet(all.filter((x) => match(x, 'stage')), 'stage'),
        grades: facet(all.filter((x) => match(x, 'grade')), 'grade'),
        subjects: facet(all.filter((x) => match(x, 'subject')), 'subject'),
        paperTypes: facet(all.filter((x) => match(x, 'paperType')), 'paperType'),
        textbooks: facet(all.filter((x) => match(x, 'textbook')), 'textbook'),
      },
    })
  },

  async getPaper(id) {
    await sleep(120)
    const p = SEEDS.find((s) => s.id === id)
    if (!p) throw new Error('试卷不存在')
    return clone<PaperDetail>({ ...summary(p), questions: QS.filter((q) => q.paperId === id) })
  },

  async removePaper(id) {
    await sleep(120)
    const i = SEEDS.findIndex((s) => s.id === id)
    if (i < 0) throw new Error('试卷不存在')
    SEEDS.splice(i, 1)
    for (let j = QS.length - 1; j >= 0; j--) if (QS[j].paperId === id) QS.splice(j, 1)
  },
}
