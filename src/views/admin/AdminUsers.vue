<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { adminApi, type ConsoleUser } from '@/api/admin'
import { ROLE_NAMES, type User } from '@/stores/auth'
import { STAGES } from '@/data/mock'
import LoadingState from '@/components/LoadingState.vue'
import { ago, fmtTime } from './labels'

const SUBJECTS = [...new Set(Object.values(STAGES).flat())]
const users = ref<ConsoleUser[] | null>(null)
const loadError = ref('')
const now = ref(Date.now())
const q = ref('')
const role = ref('')
const onlineOnly = ref(false)
const editing = ref('')
const busy = ref(false)
const error = ref('')
const message = ref('')
const form = reactive({ username: '', displayName: '', password: '', role: 'member' as User['role'], subjects: [] as string[], active: true })

async function load() {
  try { users.value = await adminApi.users(); now.value = Date.now(); loadError.value = '' }
  catch (e) { loadError.value = (e as Error).message }
}
const list = computed(() => (users.value ?? []).filter(u =>
  (!role.value || u.role === role.value) && (!onlineOnly.value || u.isOnline)
  && (!q.value.trim() || `${u.username} ${u.displayName}`.toLowerCase().includes(q.value.trim().toLowerCase()))))
const online = computed(() => (users.value ?? []).filter(u => u.isOnline).length)
const selected = computed(() => users.value?.find(u => u.id === editing.value))

function edit(u?: ConsoleUser) {
  editing.value = u?.id ?? ''
  Object.assign(form, { username: u?.username ?? '', displayName: u?.displayName ?? '', password: '', role: u?.role ?? 'member', subjects: [...(u?.subjects ?? [])], active: u?.active ?? true })
  error.value = ''; message.value = ''
}
async function run(fn: () => Promise<unknown>, done: string) {
  if (busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try { await fn(); message.value = done; await load() }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
const save = () => run(async () => {
  const saved = await adminApi.saveUser(editing.value, { ...form, password: form.password || undefined })
  editing.value = saved.id; form.password = ''
}, editing.value ? '已保存，该用户需重新登录' : '账号已创建')
function signOut() {
  const u = selected.value
  if (u && window.confirm(`让「${u.displayName}」在所有设备上退出登录？`)) run(() => adminApi.signOutUser(u.id), '已强制下线')
}
function remove() {
  const u = selected.value
  if (!u || !window.confirm(`删除「${u.displayName}（${u.username}）」？删除后无法恢复。已有试卷和题目会保留，归属将解除。`)) return
  run(async () => { await adminApi.deleteUser(u.id); edit() }, '账号已删除')
}

let timer: ReturnType<typeof setInterval> | undefined
const refresh = () => { if (document.visibilityState === 'visible') void load() }
onMounted(() => { load(); timer = setInterval(refresh, 30_000); document.addEventListener('visibilitychange', refresh) })
onUnmounted(() => { clearInterval(timer); document.removeEventListener('visibilitychange', refresh) })
</script>

<template>
  <main class="adm-page">
    <div class="adm-head">
      <div><h1>用户与在线状态</h1><p>题库账号的角色与学科授权，与题库内「账号管理」一致。在线状态每 30 秒刷新，90 秒内无活动视为离线。</p></div>
      <button class="btn btn-sm" @click="edit()">＋ 新建账号</button>
    </div>
    <div class="adm-split">
      <section class="adm-card">
        <div class="adm-toolbar">
          <input v-model="q" type="search" placeholder="搜索账号或姓名" aria-label="搜索账号">
          <select v-model="role" aria-label="角色"><option value="">全部角色</option><option v-for="(name, key) in ROLE_NAMES" :key="key" :value="key">{{ name }}</option></select>
          <label class="adm-check"><input v-model="onlineOnly" type="checkbox">只看在线</label>
          <span class="adm-badge ok"><i class="adm-dot"></i>{{ online }} 人在线</span>
        </div>
        <p v-if="loadError" class="adm-error" role="alert">{{ loadError }} <button class="btn-link" @click="load">重试</button></p>
        <LoadingState v-if="!users && !loadError" :rows="6" />
        <div v-else-if="users" class="adm-table-wrap">
          <table class="adm-table">
            <thead><tr><th>账号</th><th>角色 / 学科</th><th>在线状态</th><th class="num">会话</th><th class="num">上传</th></tr></thead>
            <tbody>
              <tr v-for="(u, i) in list" :key="u.id" class="clickable anim-fade" :style="{ animationDelay: `${Math.min(i, 12) * 20}ms` }" :class="{ selected: editing === u.id }" tabindex="0" @click="edit(u)" @keydown.enter="edit(u)">
                <td><b>{{ u.displayName }}</b><span class="sub">{{ u.username }}{{ u.active ? '' : ' · 已停用' }}</span></td>
                <td>{{ ROLE_NAMES[u.role] }}<span class="sub">{{ u.role === 'admin' ? '全部学科' : u.subjects.join('、') }}</span></td>
                <td><span class="presence" :class="{ on: u.isOnline }" :title="u.lastSeenAt ? `最后在线：${fmtTime(u.lastSeenAt)}` : ''"><i class="adm-dot"></i>{{ u.isOnline ? '在线' : u.lastSeenAt ? `${ago(u.lastSeenAt, now)}在线` : '从未登录' }}</span></td>
                <td class="num">{{ u.sessions }}</td>
                <td class="num">{{ u.uploads }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="!list.length" class="adm-empty">没有符合条件的账号</p>
        </div>
      </section>

      <section class="adm-card editor">
        <h2>{{ editing ? '编辑账号' : '新建账号' }}</h2>
        <form class="adm-form" @submit.prevent="save">
          <p v-if="selected" class="adm-hint">{{ selected.isOnline ? '当前在线' : `最后在线：${fmtTime(selected.lastSeenAt)}` }} · {{ selected.sessions }} 个有效会话</p>
          <label>账号<input v-model="form.username" required pattern="[a-zA-Z0-9_.\-]{3,64}" :disabled="!!editing" placeholder="3–64 位字母、数字或 . _ -"></label>
          <label>姓名<input v-model="form.displayName" required maxlength="64"></label>
          <label>{{ editing ? '重置密码（留空保留原密码）' : '初始密码' }}<input v-model="form.password" type="password" autocomplete="new-password" :required="!editing" minlength="10" maxlength="128" placeholder="至少 10 位"></label>
          <label>角色<select v-model="form.role"><option v-for="(name, key) in ROLE_NAMES" :key="key" :value="key">{{ name }}</option></select></label>
          <fieldset v-if="form.role !== 'admin'" class="subjects"><legend>授权学科（至少一个）</legend><label v-for="s in SUBJECTS" :key="s" class="check"><input v-model="form.subjects" type="checkbox" :value="s">{{ s }}</label></fieldset>
          <label class="check"><input v-model="form.active" type="checkbox">启用账号</label>
          <p v-if="error" class="adm-error" role="alert">{{ error }}</p>
          <p v-if="message" class="adm-ok" role="status">{{ message }}</p>
          <div class="adm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy || (form.role !== 'admin' && !form.subjects.length)">{{ busy ? '处理中…' : '保存账号' }}</button>
            <template v-if="selected">
              <button type="button" class="btn btn-sm" :disabled="busy || !selected.sessions" @click="signOut">强制下线</button>
              <button type="button" class="btn btn-sm btn-danger" :disabled="busy" @click="remove">删除</button>
            </template>
          </div>
          <p class="adm-hint">修改角色、学科或密码后，该用户需重新登录。</p>
        </form>
      </section>
    </div>
  </main>
</template>

<style scoped>
.presence { display: inline-flex; align-items: center; gap: 6px; color: var(--c-text-4); font-size: 12px; white-space: nowrap; }
.presence.on { color: var(--c-success); }
.subjects { border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: 10px 12px; }
.subjects legend { font-size: 12px; color: var(--c-text-3); }
.subjects .check { margin: 4px 12px 4px 0; }
.editor { position: sticky; top: 16px; }
@media (max-width: 960px) { .editor { position: static; } }
</style>
