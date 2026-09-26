<script setup lang="ts">
import type { JobListItem } from '@/api/parse'
import { STAGE_SHORT, STATUS_LABELS } from '@/utils/stages'

defineProps<{ jobs: JobListItem[]; total: number; active: number; busy: Set<string> }>()
defineEmits<{ open: [id: string]; retry: [id: string]; cancel: [id: string] }>()

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
  <div class="card panel">
    <div class="head">
      <span class="card-title">解析任务</span>
      <span v-if="active" class="live">{{ active }} 份进行中</span>
      <span v-else-if="total" class="muted small">共 {{ total }} 份</span>
    </div>
    <p v-if="!jobs.length" class="muted small">暂无记录。可一次拖入多份试卷，在后台依次解析。</p>
    <ul v-else class="list">
      <li v-for="j in jobs" :key="j.id" class="item" :class="j.status">
        <button class="main" :title="j.fileName" @click="$emit('open', j.id)">
          <span class="name">{{ j.fileName }}</span>
          <span class="meta">
            <b class="status">{{ STATUS_LABELS[j.status] }}</b>
            <span class="detail">{{ j.status === 'done' || j.status === 'failed' || j.status === 'running' ? meta(j) : '' }}</span>
            <span class="time">{{ DATE.format(new Date(j.createdAt)) }}</span>
          </span>
          <span v-if="j.status === 'running'" class="bar"><span :style="{ width: j.progress + '%' }" /></span>
        </button>
        <button v-if="j.status === 'queued'" class="btn-link small act" :disabled="busy.has('job:' + j.id)" @click="$emit('cancel', j.id)">取消</button>
        <button v-else-if="j.status === 'failed' || j.status === 'cancelled'" class="btn-link is-primary small act" :disabled="busy.has('job:' + j.id)" @click="$emit('retry', j.id)">重试</button>
      </li>
    </ul>
    <p v-if="total > jobs.length" class="muted small">仅显示最近 {{ jobs.length }} 份</p>
  </div>
</template>

<style scoped>
.panel { padding: 18px; display: flex; flex-direction: column; gap: 10px; }
.head { display: flex; align-items: baseline; justify-content: space-between; }
.live { font-size: 12px; color: var(--c-primary); }
.small { font-size: 12px; margin: 0; line-height: 1.6; }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; max-height: 420px; overflow-y: auto; }
.item { display: flex; align-items: flex-start; gap: 8px; border-top: 1px solid var(--c-divider); }
.item:first-child { border-top: none; }
.main { flex: 1; min-width: 0; text-align: left; border: none; background: transparent; padding: 8px 0; display: flex; flex-direction: column; gap: 4px; color: var(--c-ink); }
.main:hover .name { color: var(--c-primary); }
.name { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { display: flex; gap: 8px; font-size: 12px; color: var(--c-text-4); align-items: baseline; min-width: 0; }
.status { font-weight: 500; flex-shrink: 0; }
.detail { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.time { flex-shrink: 0; font-variant-numeric: tabular-nums; }
.item.running .status, .item.queued .status { color: var(--c-primary); }
.item.done .status { color: #3F7340; }
.item.failed .status, .item.failed .detail { color: #A0301F; }
.bar { height: 3px; border-radius: 2px; background: var(--c-divider); overflow: hidden; }
.bar > span { display: block; height: 100%; background: var(--c-primary); transition: width .3s; }
.act { padding: 10px 0 0; flex-shrink: 0; }
</style>
