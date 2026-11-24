<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { adminApi, type ExamPage } from '@/api/admin'
import LoadingState from '@/components/LoadingState.vue'
import { STAGES } from '@/data/mock'
import { formatCost } from '@/utils/usage'
import { EXAM_STATUS, fmtTime } from './labels'

const router = useRouter()
const SUBJECTS = [...new Set(Object.values(STAGES).flat())]
const PAGE = 20
const q = ref('')
const status = ref('')
const subject = ref('')
const offset = ref(0)
const data = ref<ExamPage | null>(null)
const loading = ref(false)
const error = ref('')
let seq = 0

async function load() {
  const id = ++seq
  loading.value = true; error.value = ''
  try {
    const page = await adminApi.exams({ q: q.value.trim(), status: status.value, subject: subject.value, limit: PAGE, offset: offset.value })
    if (id === seq) data.value = page
  } catch (e) { if (id === seq) error.value = (e as Error).message }
  finally { if (id === seq) loading.value = false }
}
let timer: ReturnType<typeof setTimeout> | undefined
watch(q, () => { clearTimeout(timer); timer = setTimeout(() => { offset.value = 0; load() }, 300) })
watch([status, subject], () => { offset.value = 0; load() })
onMounted(load)

const totalAll = computed(() => Object.values(data.value?.byStatus ?? {}).reduce((a, b) => a + b, 0))
function go(delta: number) { offset.value = Math.max(0, offset.value + delta * PAGE); load() }
</script>

<template>
  <main class="adm-page">
    <div class="adm-head">
      <div><h1>试卷管理</h1><p>全部学科上传的试卷：查看解析结果与 AI 费用，修改试卷属性、重试、移出试卷库或删除。</p></div>
    </div>
    <section class="adm-card">
      <div class="adm-toolbar">
        <input v-model="q" type="search" placeholder="搜索文件名、试卷名称或 ID" aria-label="搜索试卷">
        <select v-model="subject" aria-label="学科"><option value="">全部学科</option><option v-for="s in SUBJECTS" :key="s" :value="s">{{ s }}</option></select>
        <div class="chips" role="group" aria-label="状态">
          <button class="chip" :class="{ 'is-solid': !status }" @click="status = ''">全部 {{ totalAll }}</button>
          <template v-for="(label, key) in EXAM_STATUS" :key="key">
            <button v-if="data?.byStatus[key]" class="chip" :class="{ 'is-solid': status === key }" @click="status = key">{{ label.text }} {{ data.byStatus[key] }}</button>
          </template>
        </div>
      </div>
      <p v-if="error" class="adm-error" role="alert">{{ error }}</p>
      <LoadingState v-if="!data && !error" :rows="6" />
      <div v-else-if="data" class="adm-table-wrap" :class="{ stale: loading }">
        <table class="adm-table">
          <thead><tr><th>试卷</th><th>学科</th><th>上传者</th><th>状态</th><th class="num">题目 / 入库</th><th class="num">页数</th><th class="num">AI 费用</th><th>上传时间</th></tr></thead>
          <tbody>
            <tr v-for="e in data.items" :key="e.id" class="clickable" tabindex="0" @click="router.push(`/admin/exams/${e.id}`)" @keydown.enter="router.push(`/admin/exams/${e.id}`)">
              <td class="title-cell"><b>{{ e.title || e.fileName }}</b><span class="sub">{{ e.title ? e.fileName + ' · ' : '' }}{{ e.fileType.toUpperCase() }}</span></td>
              <td>{{ e.subject || '—' }}</td>
              <td>{{ e.owner?.displayName ?? '—' }}<span v-if="e.owner" class="sub">{{ e.owner.username }}</span></td>
              <td><span class="adm-badge" :class="EXAM_STATUS[e.status]?.tone">{{ EXAM_STATUS[e.status]?.text ?? e.status }}{{ e.status === 'running' ? ` ${e.progress}%` : '' }}</span></td>
              <td class="num">{{ e.questionCount }} / {{ e.bankCount }}</td>
              <td class="num">{{ e.pageCount ?? '—' }}</td>
              <td class="num">{{ e.calls ? formatCost(e.cost, e.currency) : '—' }}</td>
              <td>{{ fmtTime(e.createdAt) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!data.items.length" class="adm-empty">没有符合条件的试卷</p>
        <div v-if="data.total > PAGE" class="adm-pager">
          <span>第 {{ offset + 1 }}–{{ Math.min(offset + PAGE, data.total) }} 份，共 {{ data.total }} 份</span>
          <span class="adm-actions"><button class="btn btn-sm" :disabled="offset === 0 || loading" @click="go(-1)">上一页</button><button class="btn btn-sm" :disabled="offset + PAGE >= data.total || loading" @click="go(1)">下一页</button></span>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.chips { display: flex; gap: 4px; flex-wrap: wrap; }
.title-cell { max-width: 360px; }
.title-cell b { font-weight: 500; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stale { opacity: .6; transition: opacity .2s; }
</style>
