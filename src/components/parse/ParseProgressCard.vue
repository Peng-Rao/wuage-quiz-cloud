<script setup lang="ts">
import { computed } from 'vue'
import type { ParseJob, ParseStage } from '@/api/parse'
import type { ParsePhase } from '@/stores/parseJob'

const props = defineProps<{
  phase: ParsePhase
  job: ParseJob | null
  /** 0–100，含上传阶段 */
  pct: number
  uploadPct: number
  fileLabel: string
  fileSize: string
  error?: string
}>()
defineEmits<{ retry: [] }>()

const STAGE_LABELS: Record<ParseStage, string> = {
  ocr: '版面识别与文字提取（OCR）',
  classify: '试卷分类：学段 / 学科 / 类型',
  segment: '题目切分与题型判断',
  knowledge: '知识点标注',
  difficulty: '难度评估',
}

const kind = computed(() => {
  const t = props.job?.fileType
  return t === 'docx' ? 'DOC' : t === 'image' ? 'IMG' : (props.fileLabel.split('.').pop() ?? 'PDF').toUpperCase().slice(0, 4)
})

const rows = computed(() => {
  const upload = {
    key: 'upload', label: '上传文件',
    status: props.phase === 'uploading' ? 'running' : 'done',
    note: props.phase === 'uploading' ? `${props.uploadPct}%` : '已上传',
  }
  const stages = (props.job?.stages ?? []).map(s => ({
    key: s.stage, label: STAGE_LABELS[s.stage], status: s.status,
    note: s.status === 'done' ? s.note ?? '' : s.status === 'running' ? '进行中…' : s.status === 'skipped' ? '已跳过' : '',
  }))
  return [upload, ...stages]
})

const meta = computed(() => [props.job?.pageCount ? `${props.job.pageCount} 页` : '', props.fileSize].filter(Boolean).join(' · '))
</script>

<template>
  <div class="card parsing">
    <div class="file">
      <div class="file-icon">{{ kind }}</div>
      <div class="file-info">
        <span class="file-name">{{ job?.fileName ?? fileLabel }}</span>
        <span class="file-meta">{{ meta }}</span>
      </div>
      <span class="pct">{{ pct }}%</span>
    </div>
    <div class="progress" role="progressbar" :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100">
      <div :class="{ failed: phase === 'failed' }" :style="{ width: pct + '%' }" />
    </div>
    <div class="tasks">
      <div v-for="t in rows" :key="t.key" class="task" :class="t.status">
        <span class="dot">{{ t.status === 'done' ? '✓' : t.status === 'running' ? '…' : t.status === 'failed' ? '!' : '' }}</span>
        <span class="task-label">{{ t.label }}</span>
        <span class="task-note">{{ t.note }}</span>
      </div>
    </div>
    <div v-if="phase === 'failed'" class="fail">
      <span>{{ error || '解析失败' }}</span>
      <button class="btn btn-outline" @click="$emit('retry')">重新上传</button>
    </div>
  </div>
</template>

<style scoped>
.parsing { padding: 28px 32px; display: flex; flex-direction: column; gap: 22px; max-width: 760px; width: 100%; margin: 12px auto 0; border-radius: 14px; }
.file { display: flex; align-items: center; gap: 14px; }
.file-icon {
  width: 44px; height: 52px; border-radius: var(--r-sm); background: var(--c-primary-soft); color: var(--c-primary);
  display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0;
}
.file-info { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; }
.file-name { font-size: 15px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-meta { font-size: 12px; color: var(--c-text-4); }
.pct { font-size: 22px; font-weight: 700; color: var(--c-primary); font-variant-numeric: tabular-nums; }
.progress { height: 6px; border-radius: 3px; background: var(--c-divider); overflow: hidden; }
.progress > div { height: 100%; background: var(--c-primary); transition: width .09s linear; }
.progress > div.failed { background: var(--c-hard); }
.tasks { display: flex; flex-direction: column; gap: 12px; }
.task { display: flex; align-items: center; gap: 12px; font-size: 14px; color: var(--c-text-4); }
.task.done, .task.running, .task.failed { color: var(--c-ink); }
.dot {
  width: 22px; height: 22px; border-radius: 11px; flex-shrink: 0; display: flex; align-items: center; justify-content: center;
  font-size: 12px; background: var(--c-divider); color: var(--c-primary-dark);
}
.task.running .dot { background: var(--c-primary-soft); }
.task.done .dot { background: var(--c-primary); color: #fff; }
.task.failed .dot { background: #FBEAE6; color: #A0301F; }
.task.skipped .task-label { text-decoration: line-through; }
.task-label { flex: 1; }
.task-note { font-size: 12px; color: var(--c-text-4); }
.fail { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 14px; color: #A0301F; }
</style>
