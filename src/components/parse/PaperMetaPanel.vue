<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { PaperMeta } from '@/api/parse'
import { STAGES } from '@/data/mock'

const props = defineProps<{ meta: PaperMeta }>()
const emit = defineEmits<{ change: [meta: PaperMeta] }>()

const GRADES: Record<string, string[]> = {
  小学: ['一年级', '二年级', '三年级', '四年级', '五年级', '六年级'],
  初中: ['初一', '初二', '初三'],
  高中: ['高一', '高二', '高三'],
}
const PAPER_TYPES = ['期中考试', '期末考试', '月考', '单元测试', '模拟考试', '高考真题', '中考真题']
const TEXTBOOKS = ['人教A版（2019）', '人教B版（2019）', '北师大版（2019）', '苏教版（2019）', '湘教版（2019）']

const FIELDS: { key: keyof PaperMeta; label: string }[] = [
  { key: 'stage', label: '学段' },
  { key: 'subject', label: '学科' },
  { key: 'grade', label: '年级' },
  { key: 'paperType', label: '试卷类型' },
  { key: 'region', label: '地区' },
  { key: 'schoolYear', label: '学年' },
  { key: 'textbook', label: '教材版本' },
]

/** 有候选项的字段用下拉，其余自由输入 */
const choices = computed<Partial<Record<keyof PaperMeta, string[]>>>(() => ({
  stage: Object.keys(STAGES),
  subject: STAGES[props.meta.stage] ?? [],
  grade: GRADES[props.meta.stage] ?? [],
  paperType: PAPER_TYPES,
  textbook: TEXTBOOKS,
}))

const editing = ref<keyof PaperMeta | null>(null)
// 编辑控件位于 v-for 内，用函数 ref 取当前唯一的那一个
let input: HTMLInputElement | HTMLSelectElement | null = null
const setInput = (el: unknown) => (input = el as typeof input)

async function edit(key: keyof PaperMeta) {
  editing.value = key
  await nextTick()
  input?.focus()
}

function commit(key: keyof PaperMeta, value: string) {
  editing.value = null
  value = value.trim()
  if (!value || value === props.meta[key]) return
  const next = { ...props.meta, [key]: value }
  // 切换学段后，学科 / 年级需要落在新学段的候选内
  if (key === 'stage') {
    if (!STAGES[value]?.includes(next.subject)) next.subject = STAGES[value]?.[0] ?? ''
    if (!GRADES[value]?.includes(next.grade)) next.grade = GRADES[value]?.[0] ?? ''
  }
  emit('change', next)
}
</script>

<template>
  <div class="card panel">
    <div class="panel-head">
      <span class="card-title">自动分类</span>
      <span class="muted small">点击可修改</span>
    </div>
    <div v-for="f in FIELDS" :key="f.key" class="meta-row">
      <span class="meta-k">{{ f.label }}</span>
      <template v-if="editing === f.key">
        <select
          v-if="choices[f.key]" :ref="setInput" class="meta-v editing" :value="meta[f.key]"
          @change="commit(f.key, ($event.target as HTMLSelectElement).value)" @blur="editing = null"
        >
          <option v-for="c in choices[f.key]" :key="c" :value="c">{{ c }}</option>
        </select>
        <input
          v-else :ref="setInput" class="meta-v editing" :value="meta[f.key]"
          @keydown.enter="commit(f.key, ($event.target as HTMLInputElement).value)"
          @keydown.esc="($event.target as HTMLInputElement).value = meta[f.key]; editing = null" @blur="commit(f.key, ($event.target as HTMLInputElement).value)"
        >
      </template>
      <button v-else class="meta-v" @click="edit(f.key)">{{ meta[f.key] || '未识别' }}</button>
    </div>
  </div>
</template>

<style scoped>
.panel { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
.panel-head { display: flex; align-items: baseline; justify-content: space-between; }
.small { font-size: 12px; }
.meta-row { display: flex; justify-content: space-between; align-items: center; font-size: 13px; gap: 10px; min-height: 26px; }
.meta-k { color: var(--c-text-3); flex-shrink: 0; }
.meta-v { padding: 3px 10px; border: 1px solid var(--c-border); border-radius: var(--r-sm); color: var(--c-ink); background: #fff; font-size: 13px; min-width: 0; }
.meta-v:hover { border-color: var(--c-primary); }
.meta-v.editing { border-color: var(--c-primary); outline: none; max-width: 170px; font-family: inherit; }
</style>
