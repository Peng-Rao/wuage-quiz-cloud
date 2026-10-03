<script setup lang="ts">
import { nextTick, reactive, ref, watch } from 'vue'
import { QUESTION_TYPES, parseApi, type DraftQuestion, type DraftQuestionPatch, type KnowledgeNodeHit, type QuestionType } from '@/api/parse'
import ModalDialog from '@/components/ModalDialog.vue'
import MathText from '@/components/MathText.vue'
import FormulaEditor from '@/components/FormulaEditor.vue'
import { MATH_RE, mathAt } from '@/utils/math'

/** 草稿题与已入库的题共用：只用到可编辑的字段 */
type EditableQuestion = Pick<DraftQuestion, 'type' | 'score' | 'material' | 'stem' | 'options' | 'answer' | 'analysis' | 'knowledgePoints'> & { no?: number }

const props = defineProps<{
  q: EditableQuestion | null
  saving: boolean
  /** 知识点联想所用的试卷（解析任务） */
  jobId: string
  title?: string
  /** 表单底部的说明，默认为草稿题的说明 */
  tip?: string
  error?: string
}>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ save: [patch: DraftQuestionPatch] }>()

const CHOICE: QuestionType[] = ['单选题', '多选题']

const form = reactive({ type: '单选题' as QuestionType, score: 5, material: '', stem: '', options: '', answer: '', analysis: '', kps: '' })

watch(() => [open.value, props.q] as const, ([v, q]) => {
  if (!v || !q) return
  Object.assign(form, {
    type: q.type, score: q.score, material: q.material ?? '', stem: q.stem, options: q.options.join('\n'),
    answer: q.answer ?? '', analysis: q.analysis ?? '', kps: q.knowledgePoints.map(k => k.name).join('、'),
  })
}, { immediate: true })

// 知识点联想：按正在输入的最后一项检索本卷知识树
const hits = ref<KnowledgeNodeHit[]>([])
let searchTimer: ReturnType<typeof setTimeout> | null = null
const SPLIT = /[、，,；;\n]/
function onKpInput() {
  if (searchTimer) clearTimeout(searchTimer)
  const last = form.kps.split(SPLIT).pop()?.trim() ?? ''
  if (!last || !props.jobId) {
    hits.value = []
    return
  }
  searchTimer = setTimeout(async () => {
    hits.value = await parseApi.searchKnowledge(last, { jobId: props.jobId }).catch(() => [])
  }, 250)
}
function pickHit(h: KnowledgeNodeHit) {
  const parts = form.kps.split(SPLIT).map(s => s.trim()).filter(Boolean)
  parts.pop()
  form.kps = [...parts, h.name].join('、') + '、'
  hits.value = []
}

// ---------- 公式 ----------

type MathField = 'stem' | 'options' | 'answer' | 'analysis'
const inputs: Partial<Record<MathField, HTMLTextAreaElement | HTMLInputElement>> = {}
const setInput = (f: MathField) => (el: unknown) => { if (el) inputs[f] = el as HTMLTextAreaElement }
const hasMath = (t: string) => new RegExp(MATH_RE.source).test(t)

const formulaOpen = ref(false)
const formulaTex = ref('')
/** 本次插入（或替换）的位置 */
let target: { field: MathField; start: number; end: number } | null = null

/** 光标在已有公式内时修改该公式，否则在光标处（替换选中文字）插入新公式 */
function openFormula(field: MathField) {
  const el = inputs[field]
  const text = form[field]
  const start = el?.selectionStart ?? text.length, end = el?.selectionEnd ?? text.length
  const hit = mathAt(text, start)
  target = hit ? { field, start: hit.start, end: hit.end } : { field, start, end }
  formulaTex.value = hit?.tex ?? ''
  formulaOpen.value = true
}

async function insertFormula(tex: string) {
  if (!target) return
  const { field, start, end } = target
  const piece = `$${tex}$`
  form[field] = form[field].slice(0, start) + piece + form[field].slice(end)
  await nextTick()
  const el = inputs[field]
  el?.focus()
  el?.setSelectionRange(start + piece.length, start + piece.length)
}

/** 按「、」等分隔解析知识点，去重 */
function parseKps() {
  const names = [...new Set(form.kps.split(SPLIT).map(s => s.trim()).filter(Boolean))]
  // 只传普通对象（响应式代理无法被复制），id 由后端按「学科 + 名称」统一生成
  return names.map(name => ({ id: props.q?.knowledgePoints.find(k => k.name === name)?.id ?? 'kp_' + name, name }))
}

function save() {
  emit('save', {
    knowledgePoints: parseKps(),
    type: form.type,
    score: Math.max(0, Number(form.score) || 0),
    stem: form.stem.trim(),
    material: form.material.trim() || null,
    options: CHOICE.includes(form.type) ? form.options.split('\n').map(s => s.trim()).filter(Boolean) : [],
    answer: form.answer.trim() || null,
    analysis: form.analysis.trim() || null,
  })
}
</script>

<template>
  <ModalDialog v-model="open" :title="title || (q?.no ? `编辑第 ${q.no} 题` : '编辑题目')" :width="680">
    <form class="form" @submit.prevent="save">
      <div class="row2">
        <label class="field">
          <span>题型</span>
          <select v-model="form.type">
            <option v-for="t in QUESTION_TYPES" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
        <label class="field">
          <span>分值</span>
          <input v-model.number="form.score" type="number" min="0" step="1">
        </label>
      </div>
      <div v-if="q?.material || form.material" class="field">
        <span class="label-row"><label for="eq-material">阅读材料 <em>只修改本题；同一篇材料下的其他题需分别修改</em></label></span>
        <textarea id="eq-material" v-model="form.material" rows="6" class="serif" />
      </div>
      <div class="field">
        <span class="label-row"><label for="eq-stem">题干</label><button type="button" class="fx" title="插入或修改公式" @click="openFormula('stem')">∑ 公式</button></span>
        <!-- 完形填空等题只有材料和选项，题干可以为空 -->
        <textarea id="eq-stem" :ref="setInput('stem')" v-model="form.stem" rows="5" class="serif" :required="!form.material.trim()" />
        <div v-if="hasMath(form.stem)" class="pv serif"><MathText :text="form.stem" /></div>
      </div>
      <div v-if="CHOICE.includes(form.type)" class="field">
        <span class="label-row">
          <label for="eq-options">选项 <em>每行一个，不含「A．」前缀</em></label>
          <button type="button" class="fx" title="插入或修改公式" @click="openFormula('options')">∑ 公式</button>
        </span>
        <textarea id="eq-options" :ref="setInput('options')" v-model="form.options" rows="4" class="serif" />
        <div v-if="hasMath(form.options)" class="pv serif options">
          <span v-for="(o, i) in form.options.split('\n').filter((x) => x.trim())" :key="i">{{ 'ABCDEFGH'[i] }}．<MathText :text="o" /></span>
        </div>
      </div>
      <div class="field">
        <span class="label-row"><label for="eq-answer">答案</label><button type="button" class="fx" title="插入或修改公式" @click="openFormula('answer')">∑ 公式</button></span>
        <input id="eq-answer" :ref="setInput('answer')" v-model="form.answer" class="serif">
        <div v-if="hasMath(form.answer)" class="pv serif"><MathText :text="form.answer" /></div>
      </div>
      <div class="field">
        <span class="label-row"><label for="eq-analysis">解析</label><button type="button" class="fx" title="插入或修改公式" @click="openFormula('analysis')">∑ 公式</button></span>
        <textarea id="eq-analysis" :ref="setInput('analysis')" v-model="form.analysis" rows="3" class="serif" />
        <div v-if="hasMath(form.analysis)" class="pv serif"><MathText :text="form.analysis" /></div>
      </div>
      <label class="field">
        <span>知识点 <em>多个用「、」分隔</em></span>
        <input v-model="form.kps" placeholder="如：集合的基本运算、一元二次不等式" @input="onKpInput">
        <ul v-if="hits.length" class="hits">
          <li v-for="h in hits" :key="h.id"><button type="button" @click="pickHit(h)"><b>{{ h.name }}</b><span>{{ h.path }}</span></button></li>
        </ul>
      </label>
      <p class="tip">公式以 $…$ 包裹的 LaTeX 保存；把光标放在公式内再点「∑ 公式」可修改该公式。{{ tip ?? '保存后该题视为已人工核对，置信度提示将消失。' }}</p>
      <p v-if="error" class="err" role="alert">{{ error }}</p>
    </form>
    <template #footer>
      <button type="button" class="btn" @click="open = false">取消</button>
      <button type="button" class="btn btn-primary save" :disabled="saving || !form.stem.trim()" @click="save">
        {{ saving ? '保存中…' : '保存' }}
      </button>
    </template>
  </ModalDialog>
  <FormulaEditor v-model="formulaOpen" :latex="formulaTex" :editing="!!formulaTex" @insert="insertFormula" />
</template>

<style scoped>
.form { display: flex; flex-direction: column; gap: 14px; }
.row2 { display: flex; gap: 14px; }
.row2 .field { flex: 1; }
.field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--c-text-3); }
.field em { font-style: normal; color: var(--c-text-4); margin-left: 6px; }
.field input, .field select, .field textarea {
  border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: 8px 10px; font-size: 14px;
  color: var(--c-ink); background: #fff; font-family: inherit; resize: vertical;
}
.field textarea.serif, .field input.serif { font-family: var(--font-serif); line-height: 1.75; }
.field input:focus, .field select:focus, .field textarea:focus { outline: none; border-color: var(--c-primary); }
.hits { list-style: none; margin: 0; padding: 4px; border: 1px solid var(--c-border); border-radius: var(--r-sm); max-height: 180px; overflow-y: auto; }
.hits button { width: 100%; text-align: left; border: none; background: none; padding: 5px 6px; display: flex; flex-direction: column; gap: 1px; border-radius: 4px; }
.hits button:hover { background: var(--c-primary-soft); }
.hits b { font-weight: 500; color: var(--c-ink); font-size: 13px; }
.hits span { font-size: 11px; color: var(--c-text-4); }
.label-row { display: flex; align-items: center; justify-content: space-between; }
.fx {
  border: 1px solid var(--c-border); background: #fff; border-radius: var(--r-sm); padding: 2px 8px;
  font-size: 12px; color: var(--c-primary);
}
.fx:hover { border-color: var(--c-primary); background: var(--c-primary-soft); }
.pv { padding: 8px 12px; background: var(--c-paper); border-radius: var(--r-sm); font-size: 14.5px; line-height: 1.9; color: var(--c-ink); }
.pv.options { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 2px 16px; }
.tip { margin: 0; font-size: 12px; color: var(--c-text-4); line-height: 1.6; }
.err { margin: 0; font-size: 13px; color: var(--c-hard); }
.save { height: 38px; font-size: 14px; padding: 0 20px; }
.save:disabled { opacity: .6; cursor: not-allowed; }
</style>
