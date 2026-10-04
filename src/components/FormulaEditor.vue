<script setup lang="ts">
import { nextTick, ref, shallowRef, watch } from 'vue'
import type { MathfieldElement } from 'mathlive'
import ModalDialog from '@/components/ModalDialog.vue'
import MathText from '@/components/MathText.vue'

/**
 * 可视化公式编辑器（MathLive）：点工具栏按钮插入分式、根号、上下标等结构，也可直接输入 LaTeX。
 * 结果为 LaTeX（不含 $），由调用方包上 $...$ 插入题目文本。
 */
const props = defineProps<{ latex: string; editing: boolean }>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ insert: [latex: string] }>()

const host = ref<HTMLElement>()
const mf = shallowRef<MathfieldElement | null>(null)
const source = ref('')
const loadError = ref('')

/** 按需加载 MathLive；字体沿用页面已加载的 KaTeX 字体，不播放按键声音 */
let loading: Promise<typeof import('mathlive')> | null = null
function loadMathLive() {
  loading ??= import('mathlive').then((m) => {
    m.MathfieldElement.fontsDirectory = null
    m.MathfieldElement.soundsDirectory = null
    m.MathfieldElement.locale = 'zh-cn'
    return m
  })
  return loading
}

watch(open, async (v) => {
  if (!v) return
  source.value = props.latex
  loadError.value = ''
  try {
    const { MathfieldElement } = await loadMathLive()
    await nextTick()
    if (!host.value) return
    // 每次打开新建一个编辑框，避免残留上次的撤销记录
    const el = new MathfieldElement()
    el.mathVirtualKeyboardPolicy = 'manual' // 弹窗内虚拟键盘被遮挡，用下方工具栏代替
    el.smartFence = true
    el.value = props.latex
    el.addEventListener('input', () => { source.value = el.value })
    host.value.replaceChildren(el)
    mf.value = el
    requestAnimationFrame(() => el.focus())
  } catch (e) {
    loadError.value = `公式编辑器加载失败：${(e as Error).message}，可直接在下方输入 LaTeX。`
  }
})

function onSource() {
  if (mf.value && mf.value.value !== source.value) mf.value.value = source.value
}

/** #0：选中内容或占位符；#?：占位符；#@：光标左侧的一项（作为底数） */
function put(tex: string) {
  if (!mf.value) {
    source.value += tex.replace(/#[0?@]/g, '')
    return
  }
  mf.value.insert(tex, { selectionMode: 'placeholder', format: 'latex', focus: true })
  source.value = mf.value.value
}

const STRUCTURES: { label: string; tex: string; title: string }[] = [
  { label: '分式', tex: '\\frac{#0}{#?}', title: '分式' },
  { label: '√', tex: '\\sqrt{#0}', title: '平方根' },
  { label: 'ⁿ√', tex: '\\sqrt[#?]{#0}', title: 'n 次方根' },
  { label: 'x²', tex: '#@^{#?}', title: '上标 / 乘方' },
  { label: 'x₁', tex: '#@_{#?}', title: '下标' },
  { label: '|x|', tex: '\\left|#0\\right|', title: '绝对值' },
  { label: '( )', tex: '\\left(#0\\right)', title: '自动伸缩的括号' },
  { label: '{ }', tex: '\\left\\{#0\\right\\}', title: '花括号（集合）' },
  { label: 'a⃗', tex: '\\vec{#0}', title: '向量' },
  { label: 'AB̄', tex: '\\overline{#0}', title: '上划线（线段、共轭复数）' },
  { label: '分段', tex: '\\begin{cases}#? & #?\\\\#? & #?\\end{cases}', title: '分段函数 / 方程组' },
  { label: 'logₐ', tex: '\\log_{#?}#?', title: '对数' },
  { label: 'Σ', tex: '\\sum_{#?}^{#?}', title: '求和' },
  { label: '∫', tex: '\\int_{#?}^{#?}', title: '定积分' },
  { label: 'lim', tex: '\\lim_{#?}', title: '极限' },
  { label: 'C', tex: '\\mathrm{C}_{#?}^{#?}', title: '组合数' },
  { label: 'A', tex: '\\mathrm{A}_{#?}^{#?}', title: '排列数' },
]
const SYMBOLS: [string, string][] = [
  ['≤', '\\le'], ['≥', '\\ge'], ['≠', '\\ne'], ['≈', '\\approx'], ['±', '\\pm'], ['×', '\\times'], ['÷', '\\div'], ['·', '\\cdot'],
  ['∈', '\\in'], ['∉', '\\notin'], ['⊆', '\\subseteq'], ['⫋', '\\subsetneqq'], ['∪', '\\cup'], ['∩', '\\cap'], ['∅', '\\varnothing'],
  ['∞', '\\infty'], ['°', '^{\\circ}'], ['∠', '\\angle'], ['△', '\\triangle'], ['⊥', '\\perp'], ['∥', '\\parallel'],
  ['∵', '\\because'], ['∴', '\\therefore'], ['→', '\\to'], ['⇒', '\\Rightarrow'], ['⇔', '\\Leftrightarrow'], ['∀', '\\forall'], ['∃', '\\exists'],
  ['ℝ', '\\mathbf{R}'], ['ℕ', '\\mathbf{N}'], ['ℤ', '\\mathbf{Z}'], ['ℚ', '\\mathbf{Q}'],
]
const GREEK: [string, string][] = [
  ['α', '\\alpha'], ['β', '\\beta'], ['γ', '\\gamma'], ['θ', '\\theta'], ['λ', '\\lambda'], ['μ', '\\mu'], ['π', '\\pi'],
  ['ρ', '\\rho'], ['σ', '\\sigma'], ['φ', '\\varphi'], ['ω', '\\omega'], ['Δ', '\\Delta'], ['Ω', '\\Omega'],
]
const FUNCS = ['sin', 'cos', 'tan', 'ln', 'lg']

function done() {
  const tex = (mf.value?.value ?? source.value).trim()
  if (tex) emit('insert', tex)
  open.value = false
}
</script>

<template>
  <ModalDialog v-model="open" :title="editing ? '修改公式' : '插入公式'" :width="720">
    <div class="fe">
      <div class="toolbar" role="toolbar" aria-label="公式结构">
        <button v-for="s in STRUCTURES" :key="s.title" type="button" class="tb wide" :title="s.title" @click="put(s.tex)">{{ s.label }}</button>
      </div>
      <div class="toolbar" role="toolbar" aria-label="符号">
        <button v-for="[l, t] in SYMBOLS" :key="t" type="button" class="tb" :title="t" @click="put(t)">{{ l }}</button>
      </div>
      <div class="toolbar" role="toolbar" aria-label="希腊字母与函数">
        <button v-for="[l, t] in GREEK" :key="t" type="button" class="tb" :title="t" @click="put(t)">{{ l }}</button>
        <button v-for="f in FUNCS" :key="f" type="button" class="tb wide" :title="`\\${f}`" @click="put(`\\${f}`)">{{ f }}</button>
      </div>

      <div ref="host" class="field-host" />
      <p v-if="loadError" class="err">{{ loadError }}</p>

      <label class="src">
        <span>LaTeX 源码 <em>可直接编辑；也可以在上方编辑框中键入，如输入 1/2 自动成为分式</em></span>
        <textarea v-model="source" rows="2" spellcheck="false" @input="onSource" />
      </label>
      <div class="preview">
        <span class="muted">预览</span>
        <MathText v-if="source.trim()" :text="`$${source}$`" />
      </div>
    </div>
    <template #footer>
      <button type="button" class="btn" @click="open = false">取消</button>
      <button type="button" class="btn btn-primary" :disabled="!source.trim()" @click="done">{{ editing ? '更新公式' : '插入' }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.fe { display: flex; flex-direction: column; gap: 10px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 4px; }
.tb {
  min-width: 32px; height: 30px; padding: 0 6px; border: 1px solid var(--c-border); border-radius: var(--r-sm); background: var(--c-surface);
  font-family: var(--font-serif); font-size: 15px; color: var(--c-ink);
}
.tb.wide { font-family: var(--font-sans); font-size: 13px; padding: 0 8px; }
.tb:hover { border-color: var(--c-primary); color: var(--c-primary); background: var(--c-primary-soft); }
.field-host :deep(math-field) {
  display: block; width: 100%; min-height: 64px; font-size: 22px; padding: 10px 12px;
  border: 1px solid var(--c-border); border-radius: var(--r-md); background: var(--c-surface);
  --primary: var(--c-primary); --caret-color: var(--c-primary); --selection-background-color: var(--c-primary-soft);
}
.field-host :deep(math-field:focus-within) { outline: none; border-color: var(--c-primary); }
/* 虚拟键盘与菜单弹出在对话框之下会被遮挡，由上方工具栏代替 */
.field-host :deep(math-field::part(virtual-keyboard-toggle)), .field-host :deep(math-field::part(menu-toggle)) { display: none; }
.src { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--c-text-2); }
.src em { font-style: normal; font-size: 12px; color: var(--c-text-4); margin-left: 6px; }
.src textarea {
  border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: 8px 10px; resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; color: var(--c-ink);
}
.src textarea:focus { outline: none; border-color: var(--c-primary); }
.preview { display: flex; align-items: baseline; gap: 12px; min-height: 28px; font-size: 16px; }
.preview .muted { font-size: 12px; flex-shrink: 0; }
.err { margin: 0; font-size: 13px; color: var(--c-hard); }
</style>
