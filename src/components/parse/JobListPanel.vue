<script setup lang="ts">
import type { JobListItem } from '@/api/parse'
import { STAGE_SHORT, STATUS_LABELS } from '@/utils/stages'
import LoadingState from '@/components/LoadingState.vue'
import { computed } from 'vue'
import type { JobFilter } from '@/stores/parseJob'

const props = defineProps<{
  jobs: JobListItem[]; total: number; active: number; busy: Set<string>; loading?: boolean
  page: number; pageSize: number; filter: JobFilter; error?: string
}>()
defineEmits<{
  open: [id: string]; retry: [id: string]; cancel: [id: string]
  page: [page: number]; filter: [filter: JobFilter]; reload: []
}>()

const filters: JobFilter[] = ['全部', '进行中', '已完成', '需处理']
const pageCount = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const pageNumbers = computed(() => {
  const start = Math.max(1, Math.min(props.page - 2, pageCount.value - 4))
  return Array.from({ length: Math.min(5, pageCount.value) }, (_, i) => start + i)
})
const rangeStart = computed(() => props.total ? (props.page - 1) * props.pageSize + 1 : 0)
const rangeEnd = computed(() => Math.min(props.page * props.pageSize, props.total))
const fileType = (name: string) => name.split('.').pop()?.toUpperCase() || 'FILE'
const DATE = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

function meta(j: JobListItem): string {
  if (j.status === 'running') return `${j.currentStage ? STAGE_SHORT[j.currentStage] + '中' : '解析中'} · ${j.progress}%`
  if (j.status === 'done') {
    const parts = [`${j.questionCount} 题`]
    if (j.savedCount) parts.push(j.savedCount >= j.questionCount ? '已入库' : `已入库 ${j.savedCount}`)
    else if (j.reviewCount) parts.push(`待核对 ${j.reviewCount}`)
    return parts.join(' · ')
  }
  if (j.status === 'failed') return j.error || '解析失败'
  return STATUS_LABELS[j.status]
}
</script>

<template>
  <section class="card job-panel" aria-label="解析任务">
    <div class="head">
      <div class="heading"><h2 class="card-title">解析任务</h2><span class="total">共 {{ total }} 份</span></div>
      <span v-if="active" class="live"><span class="live-dot" />{{ active }} 份进行中</span>
    </div>
    <div class="toolbar">
      <div class="filters" role="group" aria-label="筛选解析任务">
        <button v-for="f in filters" :key="f" :aria-pressed="filter === f" :class="{ selected: filter === f }" @click="$emit('filter', f)">{{ f }}</button>
      </div>
      <span class="list-note">每页 {{ pageSize }} 份</span>
    </div>
    <div v-if="error" class="load-error" role="alert"><span>{{ error }}</span><button class="btn-link is-primary" :disabled="loading" @click="$emit('reload')">重新加载</button></div>
    <LoadingState v-if="loading && !jobs.length" compact class="small" label="正在加载解析任务…" />
    <div v-else-if="!jobs.length && !error" class="empty"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3h8l4 5v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V8Z"/><path d="M4 8h16M9 12h6m-6 4h4"/></svg><p>{{ filter === '全部' ? '还没有解析任务' : `暂无${filter}的试卷` }}</p><span>{{ filter === '全部' ? '上传一份试卷，解析进度与结果会显示在这里' : '可切换其他状态查看任务' }}</span></div>
    <template v-else-if="jobs.length">
      <div class="columns" aria-hidden="true"><span>试卷文件</span><span>解析状态</span><span>上传时间</span><span>操作</span></div>
      <ul class="list" :aria-busy="loading">
        <li v-for="j in jobs" :key="j.id" class="item" :class="j.status">
          <button class="main" :title="j.fileName" @click="$emit('open', j.id)">
            <span class="file-icon" :class="{ word: fileType(j.fileName) === 'DOCX', image: ['JPG', 'JPEG', 'PNG'].includes(fileType(j.fileName)) }" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8Z"/><path d="M14 3v5h5M8 13h8m-8 4h5"/></svg></span>
            <span class="file-copy"><span class="name">{{ j.fileName }}</span><span class="file-kind">{{ fileType(j.fileName) }}<span v-if="j.status === 'done'"> · {{ j.questionCount }} 道题</span></span></span>
          </button>
          <div class="state"><span class="status"><i />{{ STATUS_LABELS[j.status] }}</span>
            <span v-if="['done', 'failed', 'running'].includes(j.status)" class="detail" :title="meta(j)">{{ meta(j) }}</span>
            <span v-if="j.status === 'running'" class="bar" role="progressbar" :aria-valuenow="j.progress" :aria-valuemin="0" :aria-valuemax="100" :aria-label="j.fileName + '解析进度'"><span :style="{ width: j.progress + '%' }" /></span>
          </div>
          <time class="time" :datetime="j.createdAt">{{ DATE.format(new Date(j.createdAt)) }}</time>
          <div class="actions">
            <button v-if="j.status === 'queued'" class="act" :disabled="busy.has('job:' + j.id)" :aria-label="'取消 ' + j.fileName" @click="$emit('cancel', j.id)">取消</button>
            <button v-else-if="j.status === 'failed' || j.status === 'cancelled'" class="act primary" :disabled="busy.has('job:' + j.id)" :aria-label="'重试 ' + j.fileName" @click="$emit('retry', j.id)">重试</button>
            <button v-else class="act" :aria-label="'查看 ' + j.fileName" @click="$emit('open', j.id)">查看<svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m6 4 4 4-4 4"/></svg></button>
          </div>
        </li>
      </ul>
    </template>
    <div v-if="total > 0" class="pagination">
      <span class="page-info">第 {{ rangeStart }}–{{ rangeEnd }} 份，共 {{ total }} 份</span>
      <nav class="page-controls" aria-label="解析任务分页">
        <button class="page-btn" aria-label="上一页" :disabled="loading || page <= 1" @click="$emit('page', page - 1)"><svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m10 4-4 4 4 4"/></svg></button>
        <button v-for="p in pageNumbers" :key="p" class="page-btn" :class="{ current: p === page }" :aria-label="`第 ${p} 页`" :aria-current="p === page ? 'page' : undefined" :disabled="loading" @click="$emit('page', p)">{{ p }}</button>
        <button class="page-btn" aria-label="下一页" :disabled="loading || page >= pageCount" @click="$emit('page', page + 1)"><svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m6 4 4 4-4 4"/></svg></button>
      </nav>
    </div>
  </section>
</template>

<style scoped>
.job-panel { overflow: hidden; min-width: 0; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 20px 24px 16px; }
.heading { display: flex; align-items: center; gap: 10px; }
.total { font-size: 12px; color: var(--c-text-3); }
.live { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-primary); background: var(--c-primary-soft); padding: 5px 9px; border-radius: 6px; }
.live-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.toolbar { display: flex; align-items: center; justify-content: space-between; padding: 0 24px 16px; gap: 12px; }
.filters { display: flex; padding: 3px; background: var(--c-surface-2); border: 1px solid var(--c-divider); border-radius: 8px; gap: 2px; }
.filters button { background: transparent; color: var(--c-text-3); border: 0; border-radius: 5px; padding: 6px 14px; font-size: 13px; }
.filters button.selected { background: var(--c-surface); color: var(--c-primary); font-weight: 600; box-shadow: 0 1px 4px #1B24300D; }
.list-note { color: var(--c-text-3); font-size: 12px; }
.columns, .item { display: grid; grid-template-columns: minmax(0, 1fr) 180px 126px 62px; align-items: center; gap: 24px; padding: 12px 24px; }
.columns { background: var(--c-surface-2); border-top: 1px solid var(--c-divider); border-bottom: 1px solid var(--c-divider); color: var(--c-text-3); font-size: 12px; }
.columns > :last-child { text-align: right; }
.list { list-style: none; margin: 0; padding: 0; }
.item { min-height: 78px; border-bottom: 1px solid var(--c-divider); transition: background .15s; }
.item:last-child { border-bottom: 0; }
.item:hover { background: var(--c-surface-2); }
.main { min-width: 0; display: flex; align-items: center; gap: 12px; text-align: left; border: none; background: transparent; padding: 2px 0; color: var(--c-ink); }
.main:hover .name { color: var(--c-primary); }
.file-icon { width: 34px; height: 40px; display: grid; place-items: center; flex-shrink: 0; border: 1px solid #EEDACD; border-radius: 7px; color: var(--c-primary); background: #FCF3ED; }
.file-icon.word { color: #50749B; border-color: #D9E3EF; background: #F0F5FA; }
.file-icon.image { color: #577D60; border-color: #DBE6DD; background: #F0F6F1; }
.file-icon svg { width: 23px; height: 23px; }
.file-copy { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.name { font-size: 14px; font-weight: 500; line-height: 1.5; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-kind { color: var(--c-text-3); font-size: 11px; }
.state { display: flex; flex-direction: column; align-items: flex-start; gap: 5px; min-width: 0; }
.status { display: inline-flex; align-items: center; gap: 5px; color: var(--c-text-3); background: var(--c-divider); border-radius: 5px; padding: 3px 7px; font-size: 12px; font-weight: 500; }
.status i { width: 4px; height: 4px; border-radius: 50%; background: currentColor; }
.detail { max-width: 100%; font-size: 11px; color: var(--c-text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.time { font-size: 12px; color: var(--c-text-3); font-variant-numeric: tabular-nums; }
.item.running .status { color: var(--c-primary); background: var(--c-primary-soft); }
.item.queued .status { color: #876834; background: #F7F1E4; }
.item.done .status { color: #3F7340; background: #EEF5ED; }
.item.failed .status { color: #A0301F; background: #FBEAE6; }
.bar { align-self: stretch; height: 3px; border-radius: 2px; background: var(--c-divider); overflow: hidden; }
.bar > span { display: block; height: 100%; background: var(--c-primary); transition: width .3s; }
.actions { display: flex; justify-content: flex-end; }
.act { display: inline-flex; align-items: center; gap: 2px; padding: 7px 0 7px 7px; border: 0; background: transparent; font-size: 12px; color: var(--c-text-3); }
.act svg { width: 13px; height: 13px; }
.act:hover, .act.primary { color: var(--c-primary); }
.act:disabled { opacity: .5; cursor: wait; }
button:focus-visible { outline: 3px solid var(--c-primary-line); outline-offset: 2px; }
.small { display: block; font-size: 12px; padding: 20px 24px; }
.empty { display: flex; flex-direction: column; align-items: center; padding: 24px 20px 36px; gap: 8px; color: var(--c-text-3); text-align: center; }
.empty svg { width: 32px; height: 32px; color: var(--c-text-4); margin-bottom: 4px; }
.empty p { margin: 0; font-size: 13px; font-weight: 500; color: var(--c-text-2); }
.empty > span { font-size: 12px; line-height: 1.7; }
.load-error { display: flex; justify-content: space-between; gap: 12px; padding: 12px 24px; font-size: 12px; color: #A0301F; background: #FBEAE6; }
.pagination { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; padding: 16px 24px; border-top: 1px solid var(--c-divider); }
.page-info { color: var(--c-text-3); font-size: 12px; font-variant-numeric: tabular-nums; }
.page-controls { display: flex; gap: 4px; }
.page-btn { display: grid; place-items: center; width: 32px; height: 32px; padding: 0; border: 1px solid var(--c-border); border-radius: 6px; color: var(--c-text-2); background: var(--c-surface); font-size: 12px; }
.page-btn svg { width: 16px; height: 16px; }
.page-btn.current { color: #fff; background: var(--c-primary); border-color: var(--c-primary); }
.page-btn:hover:not(:disabled):not(.current) { color: var(--c-primary); border-color: var(--c-primary-line); background: var(--c-primary-soft); }
.page-btn:disabled { opacity: .45; cursor: default; }
@media (max-width: 900px) {
  .columns, .item { grid-template-columns: minmax(0, 1fr) 130px 112px 48px; gap: 16px; }
}
@media (max-width: 640px) {
  .head { padding: 18px 16px 14px; }
  .toolbar { padding: 0 16px 14px; flex-wrap: wrap; }
  .filters button { padding: 6px 12px; }
  .columns { display: none; }
  .pagination { padding: 14px 16px; justify-content: center; }
  .page-info { width: 100%; text-align: center; }
  .item { grid-template-columns: minmax(0, 1fr) auto; gap: 10px 12px; padding: 16px; }
  .main { grid-column: 1 / -1; }
  .name { white-space: normal; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .state { grid-column: 1; grid-row: 2; margin-left: 46px; }
  .actions { grid-column: 2; grid-row: 2 / 4; }
  .time { grid-column: 1; margin-left: 46px; font-size: 11px; }
}
</style>
