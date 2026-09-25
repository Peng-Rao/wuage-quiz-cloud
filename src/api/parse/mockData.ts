import type { DraftQuestion, PaperMeta, ParseOptions, QuestionType, RecentUpload } from './types'

type Seed = {
  type: QuestionType
  score: number
  page: number
  coef: number
  confidence: number
  stem: string
  options?: string[]
  answer: string | null
  analysis?: string
  kps: string[]
  /** 在页面上的纵向位置（0–1），用于生成原图区域 */
  y: [number, number]
  /** 查重命中的题库题目 id */
  dup?: string
}

// 与设计稿「北京市海淀区 2026—2027 学年高一上期中数学」一致
const SEEDS: Seed[] = [
  {
    type: '单选题', score: 5, page: 1, coef: 0.86, confidence: 0.98, y: [0.18, 0.3],
    stem: '已知集合 A = {−1, 0, 1, 2}，B = { x | x² ≤ 1 }，则 A ∩ B = （ ）',
    options: ['{−1, 0, 1}', '{0, 1}', '{−1, 1}', '{0, 1, 2}'],
    answer: 'A', analysis: 'B = [−1, 1]，故 A ∩ B = {−1, 0, 1}。', kps: ['集合的基本运算', '一元二次不等式'],
  },
  {
    type: '单选题', score: 5, page: 1, coef: 0.79, confidence: 0.97, y: [0.34, 0.46],
    stem: '命题“∀x > 0，x² + x > 0”的否定是（ ）',
    options: ['∃x > 0，x² + x ≤ 0', '∃x ≤ 0，x² + x ≤ 0', '∀x > 0，x² + x ≤ 0', '∀x ≤ 0，x² + x > 0'],
    answer: 'A', analysis: '全称量词命题的否定为存在量词命题，并否定结论。', kps: ['全称量词与存在量词'], dup: 'bank_20931',
  },
  {
    type: '单选题', score: 5, page: 1, coef: 0.64, confidence: 0.96, y: [0.5, 0.62],
    stem: '“a > 1”是“1/a < 1”的（ ）',
    options: ['充分不必要条件', '必要不充分条件', '充要条件', '既不充分也不必要条件'],
    answer: 'A', analysis: '1/a < 1 ⇔ a < 0 或 a > 1。', kps: ['充分条件与必要条件', '不等式性质'],
  },
  {
    type: '单选题', score: 5, page: 1, coef: 0.52, confidence: 0.71, y: [0.66, 0.8],
    stem: '若 x > 0，y > 0，且 x + 2y = 1，则 1/x + 1/y 的最小值为（ ）',
    options: ['2√2', '3 + 2√2', '4', '6'],
    answer: null, kps: ['基本不等式'],
  },
  {
    type: '多选题', score: 6, page: 2, coef: 0.41, confidence: 0.95, y: [0.1, 0.24],
    stem: '已知函数 f(x) = x² − 2ax + 3 在区间 (−∞, 2] 上单调递减，则实数 a 的取值可以是（ ）',
    options: ['1', '2', '3', '4'],
    answer: 'BCD', analysis: '对称轴 x = a ≥ 2。', kps: ['二次函数的单调性'],
  },
  {
    type: '填空题', score: 5, page: 2, coef: 0.73, confidence: 0.99, y: [0.3, 0.38],
    stem: '函数 f(x) = √(x − 1) + 1/(x − 3) 的定义域为 ________.',
    answer: '[1, 3) ∪ (3, +∞)', analysis: 'x − 1 ≥ 0 且 x − 3 ≠ 0。', kps: ['函数的概念及其表示'],
  },
  {
    type: '填空题', score: 5, page: 2, coef: 0.35, confidence: 0.68, y: [0.42, 0.52],
    stem: '已知 f(x) 是定义在 R 上的奇函数，当 x ≥ 0 时 f(x) = x² − 2x，则不等式 f(x) > x 的解集为 ________.',
    answer: null, kps: ['函数的奇偶性', '一元二次不等式'],
  },
  {
    type: '解答题', score: 12, page: 3, coef: 0.58, confidence: 0.94, y: [0.08, 0.3],
    stem: '已知集合 A = { x | x² − 4x + 3 < 0 }，B = { x | m − 1 < x < 2m + 1 }.（1）当 m = 2 时，求 A ∪ B；（2）若 A ∩ B = B，求实数 m 的取值范围.',
    answer: '（1）A ∪ B = (1, 5)；（2）m ≤ −2',
    analysis: 'A = (1, 3)。（2）A ∩ B = B 即 B ⊆ A；B 为空集时 m ≤ −2，B 非空时无解。', kps: ['集合的基本运算', '集合间的基本关系'],
  },
  {
    type: '解答题', score: 12, page: 4, coef: 0.27, confidence: 0.92, y: [0.08, 0.34],
    stem: '已知函数 f(x) = x + a/x（a > 0）.（1）判断 f(x) 在 (0, √a) 上的单调性并用定义证明；（2）若对任意 x ∈ [1, 2]，f(x) ≥ 4 恒成立，求 a 的取值范围.',
    answer: '（1）单调递减；（2）a ≥ 4',
    analysis: '（2）分离参数：a ≥ 4x − x² 在 [1, 2] 上恒成立，右侧最大值为 4。', kps: ['函数的单调性', '基本不等式', '恒成立问题'],
  },
]

const KP_IDS: Record<string, string> = {}
const kpRef = (name: string) => ({ id: (KP_IDS[name] ??= 'kp_' + (Object.keys(KP_IDS).length + 1)), name })

export function buildMockQuestions(jobId: string, opts: ParseOptions): DraftQuestion[] {
  return SEEDS.map((s, i) => ({
    id: `${jobId}_q${i + 1}`,
    jobId,
    no: i + 1,
    type: s.type,
    score: s.score,
    page: s.page,
    stem: s.stem,
    options: s.options ?? [],
    answer: opts.answer ? s.answer : null,
    analysis: opts.answer ? s.analysis ?? null : null,
    knowledgePoints: opts.knowledge ? s.kps.map(kpRef) : [],
    coef: s.coef,
    confidence: s.confidence,
    blockIds: [`${jobId}_b${i + 1}`],
    regions: [{ page: s.page, bbox: [0.08, s.y[0], 0.92, s.y[1]] }],
    images: [],
    duplicateOf: opts.dedupe ? s.dup ?? null : null,
    status: 'draft',
  }))
}

export const MOCK_PAGE_COUNT = 4

export const MOCK_META: PaperMeta = {
  stage: '高中',
  subject: '数学',
  grade: '高一',
  paperType: '期中考试',
  region: '北京 · 海淀',
  schoolYear: '2026—2027 上',
  textbook: '人教A版（2019）',
}

export const MOCK_RECENT: RecentUpload[] = [
  { jobId: 'r1', fileName: '高一数学 9 月月考（学生版）.pdf', questionCount: 18, reviewCount: 0, savedCount: 18, createdAt: '2026-09-20T10:00:00+08:00' },
  { jobId: 'r2', fileName: '暑期衔接测试卷.docx', questionCount: 22, reviewCount: 0, savedCount: 22, createdAt: '2026-08-28T10:00:00+08:00' },
  { jobId: 'r3', fileName: '集合单元小测（拍照 3 张）', questionCount: 10, reviewCount: 2, savedCount: 0, createdAt: '2026-08-25T10:00:00+08:00' },
]

/** 生成一张示意页图（SVG data URL），真实环境由后端渲染 PDF 页 */
export function mockPageImage(page: number, fileName: string): string {
  const lines = Array.from({ length: 26 }, (_, i) => {
    const y = 120 + i * 40
    const w = 520 + ((i * 37 + page * 13) % 140)
    return `<rect x="70" y="${y}" width="${w}" height="10" rx="3" fill="#E4E1D8"/>`
  }).join('')
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 1131">
<rect width="800" height="1131" fill="#fff"/>
<text x="400" y="70" text-anchor="middle" font-size="22" fill="#6A6F76" font-family="serif">${escapeXml(fileName)}</text>
${lines}
<text x="400" y="1100" text-anchor="middle" font-size="16" fill="#8A8F95">第 ${page} 页</text>
</svg>`
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
}

function escapeXml(s: string) {
  return s.replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' })[c]!)
}
