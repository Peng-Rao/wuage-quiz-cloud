import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { adminApi, type ConsoleAccount, type Permission } from '@/api/admin'

/** 管理后台登录状态（与题库账号相互独立） */
export const useConsoleStore = defineStore('console', () => {
  const account = ref<ConsoleAccount | null>(null)
  const checked = ref(false)
  const error = ref('')
  const isRoot = computed(() => !!account.value?.isRoot)

  function can(permission: Permission) {
    return !!account.value && (account.value.isRoot || account.value.permissions.includes(permission))
  }
  async function restore() {
    error.value = ''
    try { account.value = await adminApi.me() }
    catch (e) {
      account.value = null
      if ((e as Error & { status?: number }).status !== 401) error.value = '无法连接管理后台服务，请稍后重试'
    }
    finally { checked.value = true }
  }
  async function login(email: string, password: string) {
    account.value = await adminApi.login(email, password)
  }
  async function logout() {
    try { await adminApi.logout() } finally { account.value = null; window.location.replace('/admin/login') }
  }
  return { account, checked, error, isRoot, can, restore, login, logout }
})
