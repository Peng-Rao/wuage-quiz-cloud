<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import './admin.css'
import ModalDialog from '@/components/ModalDialog.vue'
import { adminApi, type Permission } from '@/api/admin'
import { useConsoleStore } from '@/stores/console'
import { THEME_LABELS, cycleTheme, themePref } from '@/utils/theme'

const c = useConsoleStore()
const route = useRoute()

const NAV: { to: string; label: string; icon: string; permission?: Permission; root?: boolean }[] = [
  { to: '/admin', label: '概览', icon: 'M3 12l9-8 9 8M5 10v10h14V10' },
  { to: '/admin/exams', label: '试卷管理', icon: 'M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7', permission: 'exams' },
  { to: '/admin/users', label: '用户与在线', icon: 'M16 19v-1a4 4 0 0 0-8 0v1M12 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6', permission: 'users' },
  { to: '/admin/keys', label: '密钥配置', icon: 'M15 7a4 4 0 1 1-3.9 4.9L4 19v2h3l1-1v-2h2v-2h2l1.1-1.1A4 4 0 0 1 15 7z', permission: 'keys' },
  { to: '/admin/pricing', label: '模型单价', icon: 'M12 3v18M17 7H9.5a3 3 0 0 0 0 6h5a3 3 0 0 1 0 6H6', permission: 'ai' },
  { to: '/admin/usage', label: 'AI 用量', icon: 'M4 20V10M10 20V4M16 20v-7M22 20H2', permission: 'ai' },
  { to: '/admin/accounts', label: '后台账号', icon: 'M12 3l8 4v5c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V7z', root: true },
  { to: '/admin/audit', label: '操作记录', icon: 'M12 7v5l3 2M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9z', root: true },
]
const items = computed(() => NAV.filter(n => (n.root ? c.isRoot : !n.permission || c.can(n.permission))))
const isActive = (to: string) => (to === '/admin' ? route.path === '/admin' : route.path === to || route.path.startsWith(to + '/'))

// ---- 修改密码 ----
const pwOpen = ref(false)
const pw = ref({ current: '', next: '', confirm: '' })
const pwError = ref('')
const pwMsg = ref('')
const pwBusy = ref(false)
function openPassword() {
  pw.value = { current: '', next: '', confirm: '' }
  pwError.value = pwMsg.value = ''
  pwOpen.value = true
}
async function savePassword() {
  if (pw.value.next !== pw.value.confirm) { pwError.value = '两次输入的新密码不一致'; return }
  pwBusy.value = true; pwError.value = ''
  try {
    await adminApi.changePassword(pw.value.current, pw.value.next)
    pwMsg.value = '密码已修改，其他设备上的登录已失效'
    pw.value = { current: '', next: '', confirm: '' }
  } catch (e) { pwError.value = (e as Error).message }
  finally { pwBusy.value = false }
}
</script>

<template>
  <div class="adm-shell">
    <aside class="side">
      <RouterLink to="/admin" class="brand" aria-label="管理后台首页">
        <svg viewBox="0 0 450 365" class="mark" fill="var(--c-logo)" aria-hidden="true">
          <rect x="0" y="0" width="87" height="365" /><rect x="0" y="0" width="450" height="95" />
          <path d="M175,133 H442 V365 H162 V283 H300 A52.5 52.5 0 0 0 300,178 H175 A22.5 22.5 0 0 1 175,133 Z" />
        </svg>
        <span><b>福格云上题库</b><small>管理后台</small></span>
      </RouterLink>
      <nav aria-label="管理后台">
        <RouterLink v-for="n in items" :key="n.to" :to="n.to" class="nav-item" :class="{ active: isActive(n.to) }" :aria-current="isActive(n.to) ? 'page' : undefined">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="n.icon" /></svg>{{ n.label }}
        </RouterLink>
      </nav>
      <div class="me">
        <div class="who">
          <span class="avatar">{{ (c.account?.displayName || '?').slice(0, 1) }}</span>
          <span class="who-text"><b>{{ c.account?.displayName }}</b><small :title="c.account?.email">{{ c.account?.email }}</small></span>
          <span v-if="c.isRoot" class="adm-badge brand">root</span>
        </div>
        <div class="me-actions">
          <button class="btn-link" type="button" @click="openPassword">修改密码</button>
          <button class="btn-link" type="button" :title="`主题：${THEME_LABELS[themePref]}`" @click="cycleTheme">{{ THEME_LABELS[themePref] }}</button>
          <button class="btn-link" type="button" @click="c.logout">退出</button>
        </div>
        <a class="to-app" href="/">返回题库 →</a>
      </div>
    </aside>
    <div class="main">
      <RouterView />
    </div>

    <ModalDialog v-model="pwOpen" title="修改密码" :width="420">
      <form id="pw-form" class="adm-form" @submit.prevent="savePassword">
        <label>当前密码<input v-model="pw.current" type="password" autocomplete="current-password" required maxlength="128"></label>
        <label>新密码<input v-model="pw.next" type="password" autocomplete="new-password" required minlength="10" maxlength="128" placeholder="至少 10 位"></label>
        <label>确认新密码<input v-model="pw.confirm" type="password" autocomplete="new-password" required minlength="10" maxlength="128"></label>
        <p v-if="pwError" class="adm-error" role="alert">{{ pwError }}</p>
        <p v-if="pwMsg" class="adm-ok" role="status">{{ pwMsg }}</p>
      </form>
      <template #footer>
        <button class="btn" type="button" @click="pwOpen = false">关闭</button>
        <button class="btn btn-primary btn-sm" form="pw-form" :disabled="pwBusy">{{ pwBusy ? '保存中…' : '保存' }}</button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.adm-shell { display: grid; grid-template-columns: 232px minmax(0, 1fr); min-height: 100vh; background: var(--c-paper); }
.side { position: sticky; top: 0; height: 100vh; display: flex; flex-direction: column; gap: 18px; padding: 20px 14px; background: var(--c-surface); border-right: 1px solid var(--c-border); }
.brand { display: flex; align-items: center; gap: 10px; padding: 4px 8px 10px; text-decoration: none; color: var(--c-ink); }
.brand:hover { text-decoration: none; color: var(--c-ink); }
.mark { width: 30px; height: 25px; flex-shrink: 0; }
.brand b { display: block; font-size: 15px; color: var(--c-primary); letter-spacing: 1px; }
.brand small { display: block; font-size: 12px; color: var(--c-text-3); margin-top: 2px; }
nav { display: flex; flex-direction: column; gap: 2px; flex: 1; overflow-y: auto; }
.nav-item { display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: var(--r-md); color: var(--c-text-2); font-size: 14px; text-decoration: none; }
.nav-item:hover { background: var(--c-paper-2); color: var(--c-ink); text-decoration: none; }
.nav-item.active { background: var(--c-primary-soft); color: var(--c-primary); font-weight: 600; }
.nav-item svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; flex-shrink: 0; }
.me { border-top: 1px solid var(--c-divider); padding: 14px 6px 0; display: flex; flex-direction: column; gap: 10px; }
.who { display: flex; align-items: center; gap: 10px; min-width: 0; }
.avatar { width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center; background: var(--c-primary-soft); color: var(--c-primary); font-weight: 600; flex-shrink: 0; }
.who-text { min-width: 0; flex: 1; }
.who-text b { display: block; font-size: 13px; }
.who-text small { display: block; font-size: 12px; color: var(--c-text-4); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.me-actions { display: flex; gap: 12px; flex-wrap: wrap; }
.to-app { font-size: 12px; color: var(--c-text-3); }
.main { min-width: 0; }
.foot .btn { height: 30px; font-size: 13px; }
:deep(.foot) { display: flex; justify-content: flex-end; gap: 8px; }

@media (max-width: 860px) {
  .adm-shell { grid-template-columns: minmax(0, 1fr); }
  .side { position: static; height: auto; min-width: 0; padding: 12px 16px; gap: 10px; border-right: none; border-bottom: 1px solid var(--c-border); }
  .brand { padding: 0; }
  nav { flex-direction: row; overflow-x: auto; flex: none; gap: 4px; padding-bottom: 2px; }
  .nav-item { white-space: nowrap; padding: 7px 10px; }
  .me { flex-direction: row; align-items: center; justify-content: space-between; flex-wrap: wrap; padding-top: 10px; }
  .to-app { display: none; }
}
</style>
