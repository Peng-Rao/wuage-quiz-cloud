<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { adminApi, type ExamDetail } from '@/api/admin'
import type { PaperMeta } from '@/api/parse'
import LoadingState from '@/components/LoadingState.vue'
import MathText from '@/components/MathText.vue'
import { GRADES, PAPER_TYPES, STAGES } from '@/data/mock'
import { STAGE_LABELS } from '@/utils/stages'
import { formatCost, formatTokens } from '@/utils/usage'
import { EXAM_STATUS, fmtSize, fmtTime } from './labels'

const props = defineProps<{ id: string }>()
const router = useRouter()
const exam = ref<ExamDetail | null>(null)
const error = ref('')
const busy = ref(false)
const actionError = ref('')
const message = ref('')
const EMPTY: PaperMeta = { title: '', stage: '', subject: '', grade: '', paperType: '', region: '', schoolYear: '', textbook: '', school: '' }
const meta = reactive<PaperMeta>({ ...EMPTY })

async function load() {
  error.value = ''
  try {
    exam.value = await adminApi.exam(props.id)
    Object.assign(meta, EMPTY, exam.value.meta ?? {})
  } catch (e) { error.value = (e as Error).message }
}
onMounted(load)

const subjects = computed(() => STAGES[meta.stage] ?? [...new Set(Object.values(STAGES).flat())])
const grades = computed(() => GRADES[meta.stage] ?? [])

async function act(fn: () => Promise<unknown>, done: string, reload = true) {
  busy.value = true; actionError.value = ''; message.value = ''
  try { await fn(); message.value = done; if (reload) await load() }
  catch (e) { actionError.value = (e as Error).message }
  finally { busy.value = false }
}
const saveMeta = () => act(() => adminApi.updateExamMeta(props.id, { ...meta }), '试卷属性已保存，已同步到入库题目')
const retry = () => act(() => adminApi.retryExam(props.id), '已重新排队解析')
const cancel = () => act(() => adminApi.cancelExam(props.id), '已取消')
function unpublish() {
  if (!window.confirm('移出试卷库：删除本卷已入库的题目，草稿保留，可在「试卷解析」中重新入库。继续？')) return
  act(async () => { const r = await adminApi.unpublishExam(props.id); message.value = `已移出 ${r.removed} 道题` }, '')
}
function remove() {
  const e = exam.value
  if (!e || !window.confirm(`彻底删除「${e.title || e.fileName}」？解析结果、草稿与 ${e.bankCount} 道已入库的题将一并删除，无法恢复。AI 费用记录会保留。`)) return
  act(async () => { await adminApi.deleteExam(props.id); router.replace('/admin/exams') }, '', false)
}
const STAGE_STATUS: Record<string, string> = { pending: '等待', running: '进行中', done: '完成', skipped: '跳过', failed: '失败' }
</script>

<template>
  <main class="adm-page">
    <RouterLink to="/admin/exams" class="back">← 试卷管理</RouterLink>
    <p v-if="error" class="adm-error" role="alert">{{ error }}</p>
    <LoadingState v-else-if="!exam" />
    <template v-else>
      <div class="adm-head">
        <div>
          <h1>{{ exam.title || exam.fileName }}</h1>
          <p>
            <span class="adm-badge" :class="EXAM_STATUS[exam.status]?.tone">{{ EXAM_STATUS[exam.status]?.text ?? exam.status }}</span>
            {{ exam.subject || '未识别学科' }} · {{ exam.owner ? `${exam.owner.displayName}（${exam.owner.username}）` : '上传者已删除' }} · {{ fmtTime(exam.createdAt) }} 上传
          </p>
        </div>
        <div class="adm-actions">
          <button v-if="exam.status === 'failed' || exam.status === 'cancelled'" class="btn btn-sm" :disabled="busy" @click="retry">重新解析</button>
          <button v-if="exam.status === 'queued'" class="btn btn-sm" :disabled="busy" @click="cancel">取消排队</button>
          <button v-if="exam.bankCount" class="btn btn-sm" :disabled="busy" @click="unpublish">移出试卷库</button>
          <button class="btn btn-sm btn-danger" :disabled="busy" @click="remove">删除试卷</button>
        </div>
      </div>
      <p v-if="actionError" class="adm-error" role="alert">{{ actionError }}</p>
      <p v-if="message" class="adm-ok" role="status">{{ message }}</p>
      <p v-if="exam.error" class="adm-error">解析错误：{{ exam.error }}</p>

      <div class="adm-split body">
        <div>
          <section class="adm-card">
            <h2>题目 <small>{{ exam.questionCount }} 道 · 已入库 {{ exam.bankCount }} 道</small></h2>
            <p v-if="!exam.questions.length" class="adm-empty">暂无解析出的题目</p>
            <ol class="questions">
              <li v-for="q in exam.questions" :key="q.id">
                <div class="q-head">
                  <b>{{ q.no }}.</b><span class="adm-badge">{{ q.type }}</span><span class="adm-badge">{{ q.score }} 分</span>
                  <span class="adm-badge" :class="q.status === 'saved' ? 'ok' : ''">{{ q.status === 'saved' ? '已入库' : '草稿' }}</span>
                  <span v-if="q.confidence < 0.8" class="adm-badge warn">待核对</span>
                  <span v-if="!q.hasAnswer" class="adm-badge">无答案</span>
                  <small>第 {{ q.page }} 页</small>
                </div>
                <MathText class="stem" :text="q.stem" :subject="exam.subject" />
                <p v-if="q.knowledge.length" class="adm-hint">知识点：{{ q.knowledge.join('、') }}</p>
              </li>
            </ol>
          </section>
        </div>
        <div>
          <section class="adm-card">
            <h2>试卷属性</h2>
            <form class="adm-form" @submit.prevent="saveMeta">
              <label>试卷名称<input v-model="meta.title" maxlength="200"></label>
              <div class="adm-grid-2">
                <label>学段<select v-model="meta.stage"><option value="">—</option><option v-for="s in Object.keys(STAGES)" :key="s">{{ s }}</option></select></label>
                <label>学科<select v-model="meta.subject"><option value="">—</option><option v-for="s in subjects" :key="s">{{ s }}</option></select></label>
                <label>年级<select v-model="meta.grade"><option value="">—</option><option v-for="g in grades" :key="g">{{ g }}</option><option v-if="meta.grade && !grades.includes(meta.grade)">{{ meta.grade }}</option></select></label>
                <label>试卷类型<select v-model="meta.paperType"><option value="">—</option><option v-for="t in PAPER_TYPES" :key="t">{{ t }}</option><option v-if="meta.paperType && !PAPER_TYPES.includes(meta.paperType)">{{ meta.paperType }}</option></select></label>
                <label>地区<input v-model="meta.region" maxlength="64"></label>
                <label>学年<input v-model="meta.schoolYear" maxlength="32" placeholder="如 2025-2026"></label>
                <label>教材版本<input v-model="meta.textbook" maxlength="64"></label>
                <label>命题学校<input v-model="meta.school" maxlength="64"></label>
              </div>
              <p class="adm-hint">修改学科后，本卷已入库的题会撤销审核，需按新学科重新分配。</p>
              <button class="btn btn-primary btn-sm" :disabled="busy">保存属性</button>
            </form>
          </section>
          <section class="adm-card">
            <h2>文件与解析</h2>
            <dl class="adm-kv">
              <dt>原始文件</dt>
              <dd><a v-for="f in exam.files" :key="f.index" class="file" :href="adminApi.examFileUrl(exam.id, f.index)">{{ f.name }}</a><span v-if="!exam.files.length">—</span></dd>
              <dt>大小</dt><dd>{{ fmtSize(exam.fileSize) }} · {{ exam.fileCount }} 个文件 · {{ exam.pageCount ?? '—' }} 页</dd>
              <dt>解析引擎</dt><dd>{{ exam.parser || '—' }}</dd>
              <dt>任务 ID</dt><dd><code>{{ exam.id }}</code></dd>
            </dl>
            <ul class="stages">
              <li v-for="s in exam.stages" :key="s.stage"><span>{{ STAGE_LABELS[s.stage as keyof typeof STAGE_LABELS] ?? s.stage }}</span><span class="adm-badge" :class="{ ok: s.status === 'done', bad: s.status === 'failed', info: s.status === 'running' }">{{ STAGE_STATUS[s.status] ?? s.status }}</span><small v-if="s.note">{{ s.note }}</small></li>
            </ul>
            <details v-if="exam.warnings.length" class="warnings"><summary>解析告警 {{ exam.warnings.length }} 条</summary><ul><li v-for="(w, i) in exam.warnings" :key="i">{{ typeof w === 'string' ? w : JSON.stringify(w) }}</li></ul></details>
          </section>
          <section class="adm-card">
            <h2>AI 用量</h2>
            <dl v-if="exam.usage" class="adm-kv">
              <dt>费用</dt><dd><b>{{ formatCost(exam.usage.cost, exam.usage.currency) }}</b><span v-if="!exam.usage.priced" class="adm-hint">（部分调用缺单价）</span></dd>
              <dt>调用</dt><dd>{{ exam.usage.calls }} 次{{ exam.usage.errors ? `（失败 ${exam.usage.errors}）` : '' }}</dd>
              <dt>Tokens</dt><dd>{{ formatTokens(exam.usage.totalTokens) }}（缓存命中 {{ formatTokens(exam.usage.cachedTokens) }}）</dd>
              <dt>MinerU</dt><dd>{{ exam.usage.pages }} 页</dd>
            </dl>
            <p v-else class="adm-hint">没有 AI 调用记录。</p>
          </section>
        </div>
      </div>
    </template>
  </main>
</template>

<style scoped>
.back { display: inline-block; font-size: 13px; color: var(--c-text-3); margin-bottom: 14px; }
.adm-head p { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.body { margin-top: 16px; }
.body .adm-card + .adm-card { margin-top: 16px; }
.questions { list-style: none; margin: 0; padding: 0; }
.questions li { padding: 14px 0; border-bottom: 1px solid var(--c-divider); }
.questions li:last-child { border-bottom: none; }
.q-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.q-head small { color: var(--c-text-4); margin-left: auto; font-size: 12px; }
.stem { font-size: 14px; line-height: 1.8; }
.file { display: block; }
.stages { list-style: none; padding: 0; margin: 14px 0 0; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.stages li { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.stages li > span:first-child { min-width: 72px; color: var(--c-text-3); }
.stages small { color: var(--c-text-4); font-size: 12px; flex-basis: 100%; padding-left: 80px; }
.warnings { margin-top: 12px; font-size: 13px; color: var(--c-text-3); }
.warnings ul { padding-left: 18px; line-height: 1.7; }
code { font-size: 12px; }
</style>
