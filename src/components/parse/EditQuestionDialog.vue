<script setup lang="ts">
import { reactive, watch } from 'vue'
import { QUESTION_TYPES, type DraftQuestion, type DraftQuestionPatch, type QuestionType } from '@/api/parse'
import ModalDialog from '@/components/ModalDialog.vue'

const props = defineProps<{ q: DraftQuestion | null; saving: boolean }>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ save: [patch: DraftQuestionPatch] }>()

const CHOICE: QuestionType[] = ['单选题', '多选题']

const form = reactive({ type: '单选题' as QuestionType, score: 5, stem: '', options: '', answer: '', analysis: '', kps: '' })

watch(() => [open.value, props.q] as const, ([v, q]) => {
  if (!v || !q) return
  Object.assign(form, {
    type: q.type, score: q.score, stem: q.stem, options: q.options.join('\n'),
    answer: q.answer ?? '', analysis: q.analysis ?? '', kps: q.knowledgePoints.map(k => k.name).join('、'),
  })
}, { immediate: true })

/** 按「、」等分隔解析知识点，去重 */
function parseKps() {
  const names = [...new Set(form.kps.split(/[、，,；;\n]/).map(s => s.trim()).filter(Boolean))]
  // 只传普通对象（响应式代理无法被复制），id 由后端按「学科 + 名称」统一生成
  return names.map(name => ({ id: props.q?.knowledgePoints.find(k => k.name === name)?.id ?? 'kp_' + name, name }))
}

function save() {
  emit('save', {
    knowledgePoints: parseKps(),
    type: form.type,
    score: Math.max(0, Number(form.score) || 0),
    stem: form.stem.trim(),
    options: CHOICE.includes(form.type) ? form.options.split('\n').map(s => s.trim()).filter(Boolean) : [],
    answer: form.answer.trim() || null,
    analysis: form.analysis.trim() || null,
  })
}
</script>

<template>
  <ModalDialog v-model="open" :title="q ? `编辑第 ${q.no} 题` : '编辑题目'" :width="680">
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
      <label class="field">
        <span>题干</span>
        <textarea v-model="form.stem" rows="5" class="serif" required />
      </label>
      <label v-if="CHOICE.includes(form.type)" class="field">
        <span>选项 <em>每行一个，不含「A．」前缀</em></span>
        <textarea v-model="form.options" rows="4" class="serif" />
      </label>
      <label class="field">
        <span>答案</span>
        <input v-model="form.answer" class="serif">
      </label>
      <label class="field">
        <span>解析</span>
        <textarea v-model="form.analysis" rows="3" class="serif" />
      </label>
      <label class="field">
        <span>知识点 <em>多个用「、」分隔</em></span>
        <input v-model="form.kps" placeholder="如：集合的基本运算、一元二次不等式">
      </label>
      <p class="tip">保存后该题视为已人工核对，置信度提示将消失。公式编辑器将在后续版本提供。</p>
    </form>
    <template #footer>
      <button type="button" class="btn" @click="open = false">取消</button>
      <button type="button" class="btn btn-primary save" :disabled="saving || !form.stem.trim()" @click="save">
        {{ saving ? '保存中…' : '保存' }}
      </button>
    </template>
  </ModalDialog>
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
.tip { margin: 0; font-size: 12px; color: var(--c-text-4); }
.save { height: 38px; font-size: 14px; padding: 0 20px; }
.save:disabled { opacity: .6; cursor: not-allowed; }
</style>
