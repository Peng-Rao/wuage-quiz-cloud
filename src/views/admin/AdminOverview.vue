<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { adminApi, type Overview } from '@/api/admin'
import { useConsoleStore } from '@/stores/console'
import LoadingState from '@/components/LoadingState.vue'
import { formatCost, formatTokens } from '@/utils/usage'
import { EXAM_STATUS } from './labels'

const c = useConsoleStore()
const data = ref<Overview | null>(null)
const error = ref('')
onMounted(async () => {
  try { data.value = await adminApi.overview() } catch (e) { error.value = (e as Error).message }
})
const worker = computed(() => {
  const w = data.value?.services.worker ?? {}
  if (w.error) return { ok: false, text: String(w.error) }
  if (w.queue === 'redis') return { ok: Number(w.workers) > 0, text: `Redis 队列 · ${w.workers} 个 Worker · ${w.busy ?? 0}/${w.slots ?? 0} 忙 · 排队 ${w.waiting ?? 0}` }
  return { ok: true, text: `进程内执行 · ${w.slots} 个执行槽` }
})
const services = computed(() => data.value ? [
  { name: '大模型', ok: data.value.services.llm },
  { name: '看图模型', ok: data.value.services.vision },
  { name: 'MinerU 云端解析', ok: data.value.services.mineru },
  { name: '向量检索', ok: data.value.services.embedding },
] : [])
</script>

<template>
  <main class="adm-page">
    <div class="adm-head">
      <div><h1>你好，{{ c.account?.displayName }}</h1><p>题库运行概况：账号、试卷、AI 费用与服务配置。</p></div>
    </div>
    <p v-if="error" class="adm-error" role="alert">{{ error }}</p>
    <LoadingState v-else-if="!data" />
    <template v-else>
      <section class="kpis">
        <RouterLink :to="c.can('users') ? '/admin/users' : '/admin'" class="adm-card kpi">
          <span class="label">在线用户</span>
          <span class="num"><i class="adm-dot live"></i>{{ data.users.online }}</span>
          <span class="sub">共 {{ data.users.total }} 个账号 · {{ data.users.active }} 个启用</span>
        </RouterLink>
        <RouterLink :to="c.can('exams') ? '/admin/exams' : '/admin'" class="adm-card kpi">
          <span class="label">上传试卷</span>
          <span class="num">{{ data.exams.total }}</span>
          <span class="sub">已入库 {{ data.exams.papers }} 份 · {{ data.exams.questions }} 道题</span>
        </RouterLink>
        <RouterLink :to="c.can('ai') ? '/admin/usage' : '/admin'" class="adm-card kpi">
          <span class="label">本月 AI 费用</span>
          <span class="num">{{ formatCost(data.ai.monthCost, data.ai.currency) }}</span>
          <span class="sub">{{ data.ai.monthCalls }} 次调用 · {{ formatTokens(data.ai.monthTokens) }} tokens · {{ data.ai.monthPages }} 页</span>
        </RouterLink>
      </section>

      <div class="adm-grid-2 lower">
        <section class="adm-card">
          <h2>试卷状态</h2>
          <ul class="status-list">
            <li v-for="(label, key) in EXAM_STATUS" :key="key">
              <span class="adm-badge" :class="label.tone">{{ label.text }}</span>
              <b>{{ data.exams.byStatus[key] ?? 0 }}</b>
            </li>
          </ul>
        </section>
        <section class="adm-card">
          <h2>服务配置</h2>
          <ul class="status-list">
            <li v-for="s in services" :key="s.name">
              <span>{{ s.name }}</span>
              <span class="adm-badge" :class="s.ok ? 'ok' : 'warn'">{{ s.ok ? '已配置' : '未配置' }}</span>
            </li>
            <li><span>任务执行</span><span class="adm-badge" :class="worker.ok ? 'ok' : 'bad'">{{ worker.text }}</span></li>
          </ul>
          <RouterLink v-if="c.can('keys')" to="/admin/keys" class="more">管理密钥配置 →</RouterLink>
        </section>
      </div>
    </template>
  </main>
</template>

<style scoped>
.kpis { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.kpi { display: flex; flex-direction: column; gap: 8px; color: var(--c-ink); text-decoration: none; transition: border-color .15s; }
.kpi:hover { border-color: var(--c-primary-line); text-decoration: none; color: var(--c-ink); }
.label { font-size: 13px; color: var(--c-text-3); }
.num { font-size: 30px; font-weight: 600; font-variant-numeric: tabular-nums; display: flex; align-items: center; gap: 10px; }
.live { color: var(--c-success); width: 10px; height: 10px; }
.sub { font-size: 12px; color: var(--c-text-4); }
.lower { margin-top: 16px; }
.lower .adm-card + .adm-card { margin-top: 0; }
.status-list { list-style: none; margin: 0; padding: 0; }
.status-list li { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--c-divider); font-size: 14px; }
.status-list li:last-child { border-bottom: none; }
.status-list b { font-variant-numeric: tabular-nums; }
.status-list .adm-badge { white-space: normal; text-align: right; }
.more { display: inline-block; margin-top: 12px; font-size: 13px; }
@media (max-width: 860px) { .kpis { grid-template-columns: 1fr; } }
</style>
