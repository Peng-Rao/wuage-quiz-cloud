<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { request } from '@/api/request'
import { ROLE_NAMES, type User, useAuthStore } from '@/stores/auth'
import { STAGES } from '@/data/mock'
const auth = useAuthStore()
const subjects = [...new Set(Object.values(STAGES).flat())]
const users = ref<User[]>([])
const error = ref('')
const message = ref('')
const busy = ref(false)
const editing = ref('')
const deleting = ref(false)
const form = reactive({ username: '', displayName: '', password: '', role: 'member' as User['role'], subjects: [] as string[], active: true })
const self = computed(() => editing.value === auth.user?.id)
async function load() { try { users.value = await request<User[]>('GET', '/api/users') } catch (e) { error.value = (e as Error).message } }
function edit(user?: User) {
  editing.value = user?.id ?? ''
  Object.assign(form, { username: user?.username ?? '', displayName: user?.displayName ?? '', password: '', role: user?.role ?? 'member', subjects: [...(user?.subjects ?? [])], active: user?.active ?? true })
  error.value = ''; message.value = ''
}
async function save() {
  if (busy.value) return
  error.value = ''; message.value = ''; busy.value = true
  try {
    const payload = { ...form, password: form.password || undefined }
    await request(editing.value ? 'PUT' : 'POST', editing.value ? `/api/users/${editing.value}` : '/api/users', payload)
    if (self.value) { window.location.replace('/login'); return }
    edit(); message.value = '账号已保存'; await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function remove() {
  const user = users.value.find(u => u.id === editing.value)
  if (!user || self.value || busy.value) return
  if (!window.confirm(`确定删除「${user.displayName}（${user.username}）」？删除后无法恢复，该用户将无法登录。已有试卷和题目会保留，题目归属将解除。`)) return
  error.value = ''; message.value = ''; busy.value = true; deleting.value = true
  try {
    await request('DELETE', `/api/users/${user.id}`)
    users.value = users.value.filter(u => u.id !== user.id)
    edit(); message.value = '账号已删除'
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false; deleting.value = false }
}
onMounted(load)
</script>
<template>
  <main class="container users-page">
    <div class="page-heading"><div><h1>账号管理</h1><p>为老师分配角色与学科，统一管理访问权限。</p></div><span class="tag">仅管理员</span></div>
    <div class="user-layout">
      <section class="card user-list"><h2>全部账号 <small>{{ users.length }}</small></h2>
        <button v-for="u in users" :key="u.id" class="user-row" :class="{ selected: editing === u.id }" :disabled="busy" @click="edit(u)">
          <span class="user-avatar">{{ u.displayName.slice(0, 1) }}</span><span class="user-info"><b>{{ u.displayName }}</b><small>{{ u.username }} · {{ ROLE_NAMES[u.role] }} · {{ u.role === 'admin' ? '全部学科' : u.subjects.join('、') }}</small></span><span class="state">{{ u.active ? '启用' : '已停用' }}</span>
        </button>
      </section>
      <form class="card editor" @submit.prevent="save">
        <div class="form-head"><h2>{{ editing ? '编辑账号' : '新建账号' }}</h2><button v-if="editing" type="button" class="btn-link" :disabled="busy" @click="edit()">新建账号</button></div>
        <label>账号<input v-model="form.username" required pattern="[a-zA-Z0-9_.\-]{3,64}" :disabled="!!editing" placeholder="3–64 位字母、数字或 . _ -"></label>
        <label>姓名<input v-model="form.displayName" required maxlength="64" placeholder="老师姓名"></label>
        <label>{{ editing ? '重置密码（留空保留原密码）' : '初始密码' }}<input v-model="form.password" type="password" autocomplete="new-password" :required="!editing" minlength="10" maxlength="128" placeholder="至少 10 位"></label>
        <label>权限角色<select v-model="form.role" :disabled="self"><option v-for="(name, value) in ROLE_NAMES" :key="value" :value="value">{{ name }}</option></select></label>
        <fieldset v-if="form.role !== 'admin'"><legend>授权学科（至少选择一个）</legend><label v-for="s in subjects" :key="s" class="check"><input v-model="form.subjects" type="checkbox" :value="s">{{ s }}</label></fieldset>
        <p class="role-note">{{ form.role === 'admin' ? '可使用全部功能，管理账号与所有学科。' : form.role === 'leader' ? '可管理授权学科的题目，上传解析、使用 AI 组卷并审核分配。' : '仅查看分配给本人且审核通过的题目及关联知识点，可手动组卷和下载。' }}</p>
        <label class="check"><input v-model="form.active" type="checkbox" :disabled="self">启用账号</label>
        <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="message" role="status">{{ message }}</p>
        <button class="btn btn-primary" :disabled="busy || (form.role !== 'admin' && !form.subjects.length)">{{ busy && !deleting ? '保存中…' : '保存账号' }}</button>
        <button v-if="editing" type="button" class="btn delete-user" :disabled="busy || self" @click="remove">{{ deleting ? '删除中…' : '删除账号' }}</button>
        <small v-if="editing && self" class="hint">不能删除自己的管理员账号。</small>
        <small class="hint">修改权限或密码后，该用户需重新登录。</small>
      </form>
    </div>
  </main>
</template>
<style scoped>
.users-page { padding-top: 32px; padding-bottom: 48px; }.page-heading,.form-head { display:flex;align-items:center;justify-content:space-between;gap:12px; }h1 { font-size:26px; margin:0 0 10px; }p { color:var(--c-text-3); font-size:14px; }h2 { font-size:17px; margin:0; }h2 small { color:var(--c-text-4); margin-left:8px; }
.user-layout { display:grid;grid-template-columns:minmax(0,1.2fr) minmax(300px,1fr);gap:24px;margin-top:24px;align-items:start; }.user-list,.editor { padding:24px; }.user-row { width:100%;display:flex;align-items:center;gap:12px;background:transparent;border:0;border-bottom:1px solid var(--c-divider);padding:20px 0;text-align:left; }.user-row.selected { background:var(--c-primary-soft); }.user-avatar { padding:10px;border-radius:50%;background:var(--c-primary-soft);color:var(--c-primary); }.user-info { flex:1;min-width:0; }.user-info small { display:block;margin-top:6px;color:var(--c-text-3);line-height:1.6; }.state { font-size:12px;color:var(--c-text-3); }.editor { display:flex;flex-direction:column;gap:18px; }label { display:flex;flex-direction:column;gap:8px;font-size:13px; }input:not([type=checkbox]),select { width:100%;padding:10px;border:1px solid var(--c-border);border-radius:6px;background:#fff; }.check { display:inline-flex;flex-direction:row;align-items:center;margin:6px 14px 6px 0; }.check input { accent-color:var(--c-primary); }fieldset { border:1px solid var(--c-border);border-radius:6px;padding:12px; }legend,.hint { font-size:12px;color:var(--c-text-3); }.role-note { font-size:12px;margin:0;line-height:1.7; }.error { color:#a0301f; }
.delete-user { color:#a0301f;border:1px solid #a0301f;background:transparent; }.delete-user:disabled { opacity:.5;cursor:not-allowed; }
@media(max-width:800px){.user-layout { grid-template-columns:1fr; }.user-list,.editor { padding:18px; }}
</style>
