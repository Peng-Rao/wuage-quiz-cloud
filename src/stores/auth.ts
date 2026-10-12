import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { request } from '@/api/request'
import { useAppStore } from './app'
import { STAGES } from '@/data/mock'

export interface User {
  id: string
  username: string
  displayName: string
  role: 'admin' | 'leader' | 'member'
  subjects: string[]
  active: boolean
}
export const ROLE_NAMES = { admin: '管理员', leader: '组长', member: '普通用户' }

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const error = ref('')
  const isAdmin = computed(() => user.value?.role === 'admin')
  const isStaff = computed(() => !!user.value && user.value.role !== 'member')
  const roleName = computed(() => user.value ? ROLE_NAMES[user.value.role] : '')

  function apply(value: User | null) {
    user.value = value
    const app = useAppStore()
    if (value && value.role !== 'admin' && !value.subjects.includes(app.subject)) {
      const subject = value.subjects[0] ?? ''
      const stage = Object.entries(STAGES).find(([, subjects]) => subjects.includes(subject))?.[0] ?? '高中'
      app.pickSubject(stage, subject)
    }
  }
  async function restore() {
    error.value = ''
    try {
      const value = await request<User>('GET', '/api/auth/me')
      if (user.value && (user.value.id !== value.id || user.value.role !== value.role || JSON.stringify(user.value.subjects) !== JSON.stringify(value.subjects))) {
        window.location.reload()
        return
      }
      apply(value)
    }
    catch (e) {
      apply(null)
      if ((e as Error & { status?: number }).status !== 401) error.value = '无法连接登录服务，请稍后重试'
    }
  }
  async function login(username: string, password: string) {
    apply(await request<User>('POST', '/api/auth/login', { username, password }))
    try { localStorage.setItem('quiz-auth-changed', String(Date.now())) } catch { /* Optional tab notification. */ }
  }
  async function logout() {
    await request('POST', '/api/auth/logout')
    try { localStorage.setItem('quiz-auth-changed', String(Date.now())) } catch { /* Optional tab notification. */ }
    window.location.replace('/login')
  }
  let heartbeatPending = false
  async function heartbeat() {
    if (!user.value || heartbeatPending) return
    heartbeatPending = true
    try { await request('POST', '/api/auth/heartbeat') }
    catch (e) {
      if ((e as Error & { status?: number }).status === 401) window.location.replace('/login')
      // Connection failures expire presence on the server; retry on the next tick.
    }
    finally { heartbeatPending = false }
  }
  return { user, error, isAdmin, isStaff, roleName, restore, login, logout, heartbeat }
})

// A cookie is shared across tabs: discard any old account's rendered state immediately.
window.addEventListener('storage', event => {
  if (event.key === 'quiz-auth-changed') window.location.reload()
})
