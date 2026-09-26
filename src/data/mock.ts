// 演示数据：结构与后端接口对齐后替换为真实请求即可。

export type QType = '单选题' | '多选题' | '填空题' | '解答题'
export type Difficulty = '容易' | '适中' | '较难'

export interface Question {
  id: number
  type: QType
  diff: Difficulty
  source: string
  uses: number
  date: string
  stem: string
  options: string[]
  answer: string
  analysis: string
  knowledge: string
}

export interface PaperItem {
  tag: string
  title: string
  region: string
  date: string
}

export const QUESTIONS: Question[] = [
  { id: 1, type: '单选题', diff: '容易', source: '2025·北京卷·高考真题', uses: 3812, date: '2026-08-21', stem: '已知集合 A = { x | x² − 3x + 2 = 0 }，B = { x | 0 < x < 5，x ∈ N }，则满足 A ⊆ C ⊆ B 的集合 C 的个数为（　　）', options: ['A．1', 'B．2', 'C．3', 'D．4'], answer: 'D', analysis: 'A = {1, 2}，B = {1, 2, 3, 4}，C 必含 1、2，3、4 可选可不选，共 2² = 4 个。', knowledge: '集合间的基本关系；子集个数' },
  { id: 2, type: '单选题', diff: '适中', source: '2026·山东济南·高一期中', uses: 1265, date: '2026-09-02', stem: '函数 f(x) = ln(x + 1) − 2/x 的零点所在的大致区间是（　　）', options: ['A．(0, 1)', 'B．(1, 2)', 'C．(2, e)', 'D．(3, 4)'], answer: 'B', analysis: 'f(1) = ln2 − 2 < 0，f(2) = ln3 − 1 > 0，由零点存在定理知零点在 (1, 2) 内。', knowledge: '函数零点存在定理' },
  { id: 3, type: '多选题', diff: '较难', source: '2026·新高考Ⅰ卷·高考真题', uses: 5420, date: '2026-06-10', stem: '已知函数 f(x) = x³ − 3x + 1，则下列说法正确的是（　　）', options: ['A．f(x) 有两个极值点', 'B．f(x) 有三个零点', 'C．点 (0, 1) 是曲线 y = f(x) 的对称中心', 'D．直线 y = −3x 是曲线 y = f(x) 的切线'], answer: 'ABC', analysis: "f'(x) = 3x² − 3，极值点 x = ±1；f(−1) = 3 > 0，f(1) = −1 < 0，结合端点趋势知有三个零点；f(x) + f(−x) = 2，故关于 (0, 1) 对称。", knowledge: '导数与极值；函数零点；函数对称性' },
  { id: 4, type: '填空题', diff: '容易', source: '2026·江苏南京·高一月考', uses: 986, date: '2026-09-12', stem: '已知向量 a = (1, 2)，b = (m, −1)，若 a ⊥ b，则 m = ________.', options: [], answer: '2', analysis: 'a · b = m − 2 = 0，得 m = 2。', knowledge: '平面向量垂直的坐标表示' },
  { id: 5, type: '单选题', diff: '容易', source: '2026·浙江杭州·高一期末', uses: 2230, date: '2026-07-01', stem: 'sin 15° · cos 15° 的值为（　　）', options: ['A．1/4', 'B．√3/4', 'C．1/2', 'D．√3/2'], answer: 'A', analysis: 'sin 15° cos 15° = ½ sin 30° = 1/4。', knowledge: '二倍角公式' },
  { id: 6, type: '解答题', diff: '适中', source: '2026·广东深圳·高二联考', uses: 1744, date: '2026-09-18', stem: '已知数列 {aₙ} 满足 a₁ = 1，aₙ₊₁ = 2aₙ + 1.（1）证明：数列 {aₙ + 1} 是等比数列；（2）求数列 {aₙ} 的前 n 项和 Sₙ.', options: [], answer: '（1）略；（2）Sₙ = 2ⁿ⁺¹ − n − 2', analysis: 'aₙ₊₁ + 1 = 2(aₙ + 1)，首项 2，公比 2，故 aₙ = 2ⁿ − 1，分组求和得 Sₙ = 2ⁿ⁺¹ − 2 − n。', knowledge: '等比数列的判定；分组求和' },
]

export const STAGES: Record<string, string[]> = {
  小学: ['语文', '数学', '英语', '科学', '道德与法治'],
  初中: ['语文', '数学', '英语', '物理', '化学', '生物', '历史', '地理', '道德与法治'],
  高中: ['语文', '数学', '英语', '物理', '化学', '生物', '历史', '地理', '政治', '信息技术'],
}

export const CHAPTERS = [
  { name: '第一章 集合与常用逻辑用语', sections: ['1.1 集合的概念', '1.2 集合间的基本关系', '1.3 集合的基本运算', '1.4 充分条件与必要条件', '1.5 全称量词与存在量词'] },
  { name: '第二章 一元二次函数、方程和不等式', sections: ['2.1 等式性质与不等式性质', '2.2 基本不等式', '2.3 二次函数与一元二次方程、不等式'] },
  { name: '第三章 函数的概念与性质', sections: ['3.1 函数的概念及其表示', '3.2 函数的基本性质', '3.3 幂函数', '3.4 函数的应用（一）'] },
  { name: '第四章 指数函数与对数函数', sections: ['4.1 指数', '4.2 指数函数', '4.3 对数', '4.4 对数函数', '4.5 函数的应用（二）'] },
  { name: '第五章 三角函数', sections: ['5.1 任意角和弧度制', '5.2 三角函数的概念', '5.3 诱导公式', '5.4 三角函数的图象与性质', '5.5 三角恒等变换'] },
]

export type FilterKey = 'type' | 'diff' | 'cat' | 'year'
export const FILTERS: { key: FilterKey; label: string; options: string[] }[] = [
  { key: 'type', label: '题型', options: ['全部', '单选题', '多选题', '填空题', '解答题'] },
  { key: 'diff', label: '难度', options: ['全部', '容易', '适中', '较难'] },
  { key: 'cat', label: '题类', options: ['全部', '高考真题', '模拟题', '期中期末', '月考', '常考题', '易错题'] },
  { key: 'year', label: '年份', options: ['全部', '2026', '2025', '2024', '更早'] },
]

export const PAPERS: Record<string, PaperItem[]> = {
  期中: [
    { tag: '期中', title: '北京市海淀区 2026—2027 学年高一上学期期中数学试题', region: '北京', date: '09-24' },
    { tag: '期中', title: '江苏省南京师大附中 2026—2027 学年高一上学期期中考试数学试卷', region: '江苏', date: '09-23' },
    { tag: '期中', title: '浙江省杭州市学军中学高二上学期期中数学试题（含答案）', region: '浙江', date: '09-22' },
    { tag: '期中', title: '广东省深圳中学 2026 届高三上学期期中数学试题', region: '广东', date: '09-21' },
    { tag: '期中', title: '湖北省武汉市部分重点中学高一上学期期中联考数学试卷', region: '湖北', date: '09-20' },
    { tag: '期中', title: '四川省成都七中高二上学期半期考试数学试题', region: '四川', date: '09-19' },
  ],
  月考: [
    { tag: '月考', title: '山东省实验中学 2027 届高三第一次月考数学试题', region: '山东', date: '09-25' },
    { tag: '月考', title: '河南省郑州一中高一 9 月月考数学试卷', region: '河南', date: '09-24' },
    { tag: '月考', title: '湖南师大附中高二上学期第一次月考数学试题', region: '湖南', date: '09-22' },
    { tag: '月考', title: '福建省福州一中高三 9 月质量检测数学试题', region: '福建', date: '09-20' },
  ],
  高考真题: [
    { tag: '真题', title: '2026 年普通高等学校招生全国统一考试（新高考Ⅰ卷）数学', region: '全国', date: '06-08' },
    { tag: '真题', title: '2026 年普通高等学校招生全国统一考试（新高考Ⅱ卷）数学', region: '全国', date: '06-08' },
    { tag: '真题', title: '2026 年高考北京卷数学真题', region: '北京', date: '06-08' },
    { tag: '真题', title: '2026 年高考天津卷数学真题', region: '天津', date: '06-08' },
  ],
  专题: [
    { tag: '专题', title: '导数压轴题专项训练：极值点偏移（30 题）', region: '通用', date: '09-18' },
    { tag: '专题', title: '数列求和方法归纳：错位相减与裂项相消', region: '通用', date: '09-15' },
    { tag: '专题', title: '圆锥曲线定点定值问题专题练习', region: '通用', date: '09-12' },
  ],
}

export const MY_PAPERS = [
  { title: '高一（3）班 集合单元测验', meta: '16 题 · 2 天前' },
  { title: '函数的概念与性质 周练', meta: '12 题 · 5 天前' },
  { title: '暑期衔接 综合卷', meta: '22 题 · 8 月 28 日' },
]

export const SCORE: Record<QType, number> = { 单选题: 5, 多选题: 6, 填空题: 5, 解答题: 12 }
export const TYPE_ORDER: QType[] = ['单选题', '多选题', '填空题', '解答题']
export const CN_NUM = ['一', '二', '三', '四']

/** 难度系数 0–1，越高越难，1 为最难（约等于 1 − 预估得分率） */
export const coefToDiff = (c: number): Difficulty => (c <= 0.3 ? '容易' : c <= 0.6 ? '适中' : '较难')
/** 「调整难度」循环时使用的代表系数 */
export const DIFF_COEFS = [0.18, 0.42, 0.68]
