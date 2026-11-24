<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { adminApi, type AuditItem } from '@/api/admin'
import LoadingState from '@/components/LoadingState.vue'
import { fmtTime } from './labels'

const ACTIONS: Record<string, string> = {
  'auth.login': '登录后台', 'auth.password': '修改密码',
  'account.create_root': '创建 root', 'account.create': '新建后台账号', 'account.update': '修改后台账号', 'account.delete': '删除后台账号',
  'user.create': '新建题库账号', 'user.update': '修改题库账号', 'user.delete': '删除题库账号', 'user.sign_out': '强制下线',
  'exam.meta': '修改试卷属性', 'exam.retry': '重新解析', 'exam.cancel': '取消解析', 'exam.unpublish': '移出试卷库', 'exam.delete': '删除试卷',
  'settings.update': '修改配置', 'settings.test': '测试连接',
  'prices.refresh': '抓取单价', 'prices.set': '设置手动单价', 'prices.clear_manual': '删除手动单价', 'prices.recompute': '重新计价',
}
const FILTERS = [['', '全部'], ['auth', '登录'], ['account', '后台账号'], ['user', '题库账号'], ['exam', '试卷'], ['settings', '配置'], ['prices', '单价']]
const PAGE = 50
const items = ref<AuditItem[] | null>(null)
const total = ref(0)
const offset = ref(0)
const action = ref('')
const error = ref('')
async function load() {
  try {
    const r = await adminApi.audit({ limit: PAGE, offset: offset.value, action: action.value || undefined })
    items.value = r.items; total.value = r.total; error.value = ''
  } catch (e) { error.value = (e as Error).message }
}
watch(action, () => { offset.value = 0; load() })
onMounted(load)
function go(d: number) { offset.value = Math.max(0, offset.value + d * PAGE); load() }
const detail = (d: Record<string, unknown> | null) => (d ? Object.entries(d).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ') : '')
</script>

<template>
  <main class="adm-page">
    <div class="adm-head"><div><h1>操作记录</h1><p>管理后台中的登录与每一次修改。密钥与密码的值不会被记录。</p></div></div>
    <section class="adm-card">
      <div class="adm-toolbar"><button v-for="[k, label] in FILTERS" :key="k" class="chip" :class="{ 'is-solid': action === k }" @click="action = k">{{ label }}</button></div>
      <p v-if="error" class="adm-error" role="alert">{{ error }}</p>
      <LoadingState v-else-if="!items" :rows="8" />
      <div v-else class="adm-table-wrap">
        <table class="adm-table">
          <thead><tr><th>时间</th><th>操作人</th><th>操作</th><th>对象</th><th>详情</th></tr></thead>
          <tbody>
            <tr v-for="a in items" :key="a.id">
              <td class="nowrap">{{ fmtTime(a.createdAt) }}</td>
              <td>{{ a.actor }}</td>
              <td class="nowrap">{{ ACTIONS[a.action] ?? a.action }}</td>
              <td>{{ a.target || '—' }}</td>
              <td class="detail">{{ detail(a.detail) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!items.length" class="adm-empty">暂无记录</p>
        <div v-if="total > PAGE" class="adm-pager">
          <span>第 {{ offset + 1 }}–{{ Math.min(offset + PAGE, total) }} 条，共 {{ total }} 条</span>
          <span class="adm-actions"><button class="btn btn-sm" :disabled="offset === 0" @click="go(-1)">上一页</button><button class="btn btn-sm" :disabled="offset + PAGE >= total" @click="go(1)">下一页</button></span>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.nowrap { white-space: nowrap; }
.detail { color: var(--c-text-3); font-size: 12px; max-width: 420px; word-break: break-all; }
</style>
