<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { bankApi, type PaperDetail } from '@/api/bank'
import type { PaperMeta } from '@/api/parse'
import { GRADES, PAPER_TYPES, STAGES, TEXTBOOKS } from '@/data/mock'
import ModalDialog from '@/components/ModalDialog.vue'

/** 管理员修改试卷属性：保存后同步到原卷解析结果与本卷已入库的题 */
const props = defineProps<{ paper: PaperDetail | null }>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ saved: [paper: PaperDetail] }>()

const EMPTY: PaperMeta = { title: '', stage: '', subject: '', grade: '', paperType: '', region: '', schoolYear: '', textbook: '', school: '' }
const form = reactive<PaperMeta>({ ...EMPTY })
const saving = ref(false)
const error = ref('')

watch(open, (v) => {
  if (!v || !props.paper) return
  Object.assign(form, EMPTY, props.paper.meta, { title: props.paper.meta.title || props.paper.title })
  error.value = ''
})

/** 候选项之外的现有值也保留在下拉中，避免打开即被改掉 */
const withCurrent = (list: string[], v: string) => (v && !list.includes(v) ? [v, ...list] : list)
const stages = computed(() => withCurrent(Object.keys(STAGES), form.stage))
const subjects = computed(() => withCurrent(STAGES[form.stage] ?? [], form.subject))

function onStage() {
  if (!STAGES[form.stage]?.includes(form.subject)) form.subject = STAGES[form.stage]?.[0] ?? ''
  if (!GRADES[form.stage]?.includes(form.grade)) form.grade = ''
}

const subjectChanged = computed(() => !!props.paper && form.subject !== props.paper.meta.subject)

async function save() {
  if (!props.paper) return
  saving.value = true
  error.value = ''
  try {
    emit('saved', await bankApi.updatePaperMeta(props.paper.id, { ...form }))
    open.value = false
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ModalDialog v-model="open" title="编辑试卷属性" :width="560">
    <form id="paper-meta-form" class="form" @submit.prevent="save">
      <label class="field">试卷名称<input v-model="form.title" class="serif" placeholder="留空则使用文件名"></label>
      <div class="row2">
        <label class="field">学段
          <select v-model="form.stage" required @change="onStage">
            <option v-for="s in stages" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="field">学科
          <select v-model="form.subject" required>
            <option v-for="s in subjects" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
      </div>
      <div class="row2">
        <label class="field">年级<input v-model="form.grade" list="paper-meta-grades"></label>
        <label class="field">试卷类型<input v-model="form.paperType" list="paper-meta-types"></label>
      </div>
      <div class="row2">
        <label class="field">地区<input v-model="form.region" placeholder="如 北京 · 海淀"></label>
        <label class="field">学年<input v-model="form.schoolYear" placeholder="如 2026—2027"></label>
      </div>
      <div class="row2">
        <label class="field">学校<input v-model="form.school" placeholder="联考等没有学校的留空"></label>
        <label class="field">教材版本<input v-model="form.textbook" list="paper-meta-textbooks"></label>
      </div>
      <datalist id="paper-meta-grades"><option v-for="g in GRADES[form.stage] ?? []" :key="g" :value="g" /></datalist>
      <datalist id="paper-meta-types"><option v-for="t in PAPER_TYPES" :key="t" :value="t" /></datalist>
      <datalist id="paper-meta-textbooks"><option v-for="t in TEXTBOOKS" :key="t" :value="t" /></datalist>
      <p class="tip">修改同步到原卷解析结果与本卷已入库的题，选题筛选、试题来源随之更新。</p>
      <p v-if="subjectChanged" class="warn">修改学科后，本卷题目将撤销审核，需在下方「审核与分配题目」中重新审核。</p>
      <p v-if="error" class="err" role="alert">{{ error }}</p>
    </form>
    <template #footer>
      <button type="button" class="btn" @click="open = false">取消</button>
      <button type="submit" form="paper-meta-form" class="btn btn-primary save" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.form { display: flex; flex-direction: column; gap: 14px; }
.row2 { display: flex; gap: 14px; }
.row2 .field { flex: 1; min-width: 0; }
.field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--c-text-3); }
.field input, .field select {
  border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: 8px 10px; font-size: 14px;
  color: var(--c-ink); background: var(--c-surface); font-family: inherit;
}
.field input.serif { font-family: var(--font-serif); }
.field input:focus, .field select:focus { outline: none; border-color: var(--c-primary); }
.tip { margin: 0; font-size: 12px; color: var(--c-text-4); line-height: 1.6; }
.warn { margin: 0; font-size: 12px; color: var(--c-accent); line-height: 1.6; }
.err { margin: 0; font-size: 13px; color: var(--c-hard); }
.save:disabled { opacity: .6; cursor: not-allowed; }
@media (max-width: 520px) {
  .row2 { flex-direction: column; }
}
</style>
