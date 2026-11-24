<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { PERMISSION_LABELS, adminApi, type ConsoleAccount, type Permission } from '@/api/admin'
import { useConsoleStore } from '@/stores/console'
import LoadingState from '@/components/LoadingState.vue'
import { ago, fmtTime } from './labels'

const c = useConsoleStore()
const accounts = ref<ConsoleAccount[] | null>(null)
const loadError = ref('')
const editing = ref('')
const busy = ref(false)
const error = ref('')
const message = ref('')
const form = reactive({ email: '', displayName: '', password: '', permissions: [] as Permission[], active: true })
const selected = computed(() => accounts.value?.find(a => a.id === editing.value))
const PERMS = Object.entries(PERMISSION_LABELS) as [Permission, string][]
const DESCRIBE: Record<Permission, string> = {
  exams: '查看全部试卷，修改属性、重试、移出试卷库与删除',
  users: '管理题库账号、角色与学科，查看在线状态、强制下线',
  keys: '查看（掩码）与修改大模型、MinerU 等 API Key 与模型',
  ai: '查看 AI 用量与成本，抓取、设置模型单价并重新计价',
}

async function load() {
  try { accounts.value = await adminApi.accounts(); loadError.value = '' } catch (e) { loadError.value = (e as Error).message }
}
onMounted(load)
function edit(a?: ConsoleAccount) {
  editing.value = a?.id ?? ''
  Object.assign(form, { email: a?.email ?? '', displayName: a?.displayName ?? '', password: '', permissions: [...(a?.isRoot ? [] : a?.permissions ?? [])], active: a?.active ?? true })
  error.value = ''; message.value = ''
}
async function save() {
  busy.value = true; error.value = ''; message.value = ''
  try {
    const saved = editing.value
      ? await adminApi.updateAccount(editing.value, { displayName: form.displayName, permissions: form.permissions, active: form.active, password: form.password || undefined })
      : await adminApi.createAccount({ email: form.email, displayName: form.displayName, password: form.password, permissions: form.permissions, active: form.active })
    if (saved.id === c.account?.id && form.password) { window.location.replace('/admin/login'); return }
    const done = editing.value ? '已保存；权限或密码变化后该账号需重新登录' : '后台账号已创建'
    await load(); edit(accounts.value?.find(a => a.id === saved.id))
    message.value = done
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function remove() {
  const a = selected.value
  if (!a || !window.confirm(`删除后台账号「${a.displayName}（${a.email}）」？该账号将立即无法登录。`)) return
  busy.value = true
  try { await adminApi.deleteAccount(a.id); edit(); message.value = '已删除'; await load() }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <main class="adm-page">
    <div class="adm-head">
      <div><h1>后台账号</h1><p>root 账号为其他人开通管理后台并按模块授权。后台账号用邮箱登录，与题库账号相互独立。</p></div>
      <button class="btn btn-sm" @click="edit()">＋ 新建后台账号</button>
    </div>
    <div class="adm-split">
      <section class="adm-card">
        <p v-if="loadError" class="adm-error" role="alert">{{ loadError }}</p>
        <LoadingState v-else-if="!accounts" :rows="3" />
        <div v-else class="adm-table-wrap">
          <table class="adm-table">
            <thead><tr><th>账号</th><th>权限</th><th>最近活动</th></tr></thead>
            <tbody>
              <tr v-for="a in accounts" :key="a.id" class="clickable" :class="{ selected: editing === a.id }" tabindex="0" @click="edit(a)" @keydown.enter="edit(a)">
                <td><b>{{ a.displayName }}</b> <span v-if="a.isRoot" class="adm-badge brand">root</span><span v-if="!a.active" class="adm-badge bad">已停用</span><span class="sub">{{ a.email }}</span></td>
                <td><span v-if="a.isRoot" class="muted">全部权限</span><span v-else-if="!a.permissions.length" class="muted">未授权</span><template v-else><span v-for="p in a.permissions" :key="p" class="adm-badge perm">{{ PERMISSION_LABELS[p] }}</span></template></td>
                <td>{{ ago(a.lastSeenAt) }}<span class="sub">登录 {{ fmtTime(a.lastLoginAt) }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <section class="adm-card">
        <h2>{{ editing ? '编辑后台账号' : '新建后台账号' }}</h2>
        <form class="adm-form" @submit.prevent="save">
          <label>邮箱<input v-model="form.email" type="email" required maxlength="254" :disabled="!!editing" placeholder="name@example.com"></label>
          <label>名称<input v-model="form.displayName" required maxlength="64"></label>
          <label>{{ editing ? '重置密码（留空保留原密码）' : '初始密码' }}<input v-model="form.password" type="password" autocomplete="new-password" :required="!editing" minlength="10" maxlength="128" placeholder="至少 10 位"></label>
          <fieldset v-if="!selected?.isRoot" class="perms">
            <legend>授权模块</legend>
            <label v-for="[key, label] in PERMS" :key="key" class="perm-row"><input v-model="form.permissions" type="checkbox" :value="key"><span><b>{{ label }}</b><small>{{ DESCRIBE[key] }}</small></span></label>
          </fieldset>
          <p v-else class="adm-hint">root 账号拥有全部权限，并且只有 root 可以管理后台账号、查看操作记录。root 不能停用或删除。</p>
          <label v-if="!selected?.isRoot" class="check"><input v-model="form.active" type="checkbox">启用账号</label>
          <p v-if="error" class="adm-error" role="alert">{{ error }}</p>
          <p v-if="message" class="adm-ok" role="status">{{ message }}</p>
          <div class="adm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy">{{ busy ? '保存中…' : '保存' }}</button>
            <button v-if="selected && !selected.isRoot" type="button" class="btn btn-sm btn-danger" :disabled="busy" @click="remove">删除</button>
          </div>
        </form>
      </section>
    </div>
  </main>
</template>

<style scoped>
.perm { margin: 2px 4px 2px 0; }
.perms { border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: 8px 12px; display: flex; flex-direction: column; gap: 4px; }
.perms legend { font-size: 12px; color: var(--c-text-3); }
.perm-row { flex-direction: row !important; align-items: flex-start; gap: 10px !important; padding: 6px 0; cursor: pointer; }
.perm-row input { margin-top: 3px; accent-color: var(--c-primary); }
.perm-row b { display: block; font-weight: 500; }
.perm-row small { display: block; color: var(--c-text-4); font-size: 12px; margin-top: 2px; line-height: 1.5; }
</style>
