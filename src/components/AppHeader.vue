<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'
import BrandLogo from './BrandLogo.vue'
import { STAGES } from '@/data/mock'
import { useAppStore } from '@/stores/app'
import { useBasketStore } from '@/stores/basket'
import { THEME_LABELS, cycleTheme, themePref } from '@/utils/theme'

const auth = useAuthStore()
const logoutError = ref('')
async function logout() {
  try { await auth.logout() } catch { logoutError.value = '退出失败，请重试' }
}
const availableStages = computed(() => Object.fromEntries(Object.entries(STAGES).map(([stage, subjects]) => [stage, subjects.filter(s => auth.isAdmin || auth.user?.subjects.includes(s))]).filter(([, subjects]) => (subjects as string[]).length)))
const app = useAppStore()
const { stage, subject } = storeToRefs(app)
const basket = useBasketStore()
const route = useRoute()
// 子页面（如 /upload/knowledge）也高亮所属菜单；首页只在根路径高亮
const isActive = (to: string) => (to === '/' ? route.path === '/' : route.path === to || route.path.startsWith(to + '/'))

const NAV = computed(() => [
  { to: '/', label: '首页' },
  { to: '/chapter', label: '章节选题' },
  { to: '/knowledge', label: '知识点选题' },
  { to: '/papers', label: '试卷选题' },
  ...(auth.isStaff ? [{ to: '/compose', label: 'AI 组卷' }] : []),
  { to: '/paper', label: '试卷编辑' },
  ...(auth.isStaff ? [{ to: '/upload', label: '试卷解析' }] : []),
  ...(auth.isAdmin ? [{ to: '/usage', label: 'AI 成本' }, { to: '/users', label: '账号管理' }] : []),
])

const open = ref(false)
const picker = ref<HTMLElement>()

function pick(s: string, sub: string) {
  app.pickSubject(s, sub)
  open.value = false
}

function onDocClick(e: MouseEvent) {
  if (open.value && picker.value && !picker.value.contains(e.target as Node)) open.value = false
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}
onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <header class="header">
    <div class="container bar">
      <BrandLogo />

      <div ref="picker" class="picker">
        <button class="picker-btn" :aria-expanded="open" @click="open = !open">
          <span class="stage">{{ stage }}</span><span>{{ subject }}</span><span class="caret">▼</span>
        </button>
        <div v-if="open" class="picker-pop">
          <div v-for="(subs, name) in availableStages" :key="name" class="picker-row">
            <span class="picker-stage">{{ name }}</span>
            <div class="picker-subs">
              <button
                v-for="s in subs" :key="s" class="chip picker-sub"
                :class="{ 'is-solid': name === stage && s === subject }"
                @click="pick(String(name), s)"
              >{{ s }}</button>
            </div>
          </div>
        </div>
      </div>

      <nav class="nav">
        <RouterLink v-for="n in NAV" :key="n.to" :to="n.to" class="nav-item" :class="{ 'is-active': isActive(n.to) }">
          {{ n.label }}
        </RouterLink>
      </nav>

      <RouterLink to="/paper" class="basket-btn">
        试题篮<span class="badge">{{ basket.count }}</span>
      </RouterLink>
      <button class="theme-btn" :title="`主题：${THEME_LABELS[themePref]}（点击切换）`" :aria-label="`主题：${THEME_LABELS[themePref]}`" @click="cycleTheme">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <template v-if="themePref === 'light'">
            <circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </template>
          <path v-else-if="themePref === 'dark'" d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
          <template v-else>
            <rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8M12 17v4" />
          </template>
        </svg>
      </button>
      <div class="account"><span>{{ auth.user?.displayName }}<small>{{ auth.roleName }}</small></span><button class="btn-link" @click="logout">退出</button><small v-if="logoutError" role="alert">{{ logoutError }}</small></div>
    </div>
  </header>
</template>

<style scoped>
.account { display:flex;align-items:center;gap:10px;font-size:12px;flex-shrink:0; }.account small { display:block;color:var(--c-text-4);margin-top:2px; }.account button { font-size:12px; }
.header { position: sticky; top: 0; z-index: 20; background: var(--c-surface); border-bottom: 1px solid var(--c-border); }
.bar { height: 60px; display: flex; align-items: center; gap: 20px; }

.picker { position: relative; flex-shrink: 0; }
.picker-btn {
  white-space: nowrap; height: 34px; padding: 0 12px; border: 1px solid var(--c-border);
  background: var(--c-paper); border-radius: var(--r-md); display: flex; align-items: center; gap: 8px;
  font-size: 14px; color: var(--c-ink);
}
.stage { color: var(--c-primary); font-weight: 600; }
.caret { font-size: 10px; color: var(--c-text-4); }
.picker-pop {
  position: absolute; top: 42px; left: 0; width: 420px; max-width: calc(100vw - 32px); background: var(--c-surface);
  border: 1px solid var(--c-border); border-radius: var(--r-lg); box-shadow: 0 12px 32px rgb(var(--shadow-rgb) / .12);
  padding: 16px; display: flex; flex-direction: column; gap: 14px;
}
.picker-row { display: flex; gap: 14px; align-items: flex-start; }
.picker-stage { width: 36px; flex-shrink: 0; font-size: 13px; color: var(--c-text-4); padding-top: 5px; }
.picker-subs { display: flex; flex-wrap: wrap; gap: 6px; }
.picker-sub { padding: 5px 10px; background: var(--c-paper); }
.picker-sub.is-solid { background: var(--c-primary); }

.nav { display: flex; gap: 2px; flex: 1; min-width: 0; overflow-x: auto; scrollbar-width: none; }
.nav-item {
  flex-shrink: 0; white-space: nowrap; height: 60px; padding: 0 12px; font-size: 15px;
  display: flex; align-items: center; color: var(--c-text-2); border-bottom: 2px solid transparent;
}
.nav-item:hover { color: var(--c-primary); text-decoration: none; }
.nav-item.is-active { color: var(--c-primary); font-weight: 600; border-bottom-color: var(--c-primary); }

.basket-btn {
  flex-shrink: 0; white-space: nowrap; height: 34px; padding: 0 14px; border: 1px solid var(--c-primary);
  background: var(--c-surface); color: var(--c-primary); border-radius: var(--r-md); font-size: 14px;
  display: flex; align-items: center; gap: 8px;
}
.basket-btn:hover { text-decoration: none; background: var(--c-primary-soft); }
.badge {
  min-width: 20px; height: 20px; border-radius: 10px; background: var(--c-inverse); color: #fff; font-size: 12px;
  display: flex; align-items: center; justify-content: center; padding: 0 6px;
}
.theme-btn {
  flex-shrink: 0; width: 34px; height: 34px; padding: 0; border: 1px solid var(--c-border); border-radius: var(--r-md);
  background: var(--c-paper); color: var(--c-text-3); display: flex; align-items: center; justify-content: center;
}
.theme-btn:hover { color: var(--c-primary); border-color: var(--c-primary-line); }
.theme-btn svg { width: 18px; height: 18px; }
.avatar {
  width: 32px; height: 32px; border-radius: 16px; background: var(--c-primary-soft); color: var(--c-primary);
  display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 600; flex-shrink: 0;
}

/* 管理员菜单项较多，中等宽度下一行放不下：菜单单独占一行 */
@media (max-width: 1180px) {
  .bar { height: auto; flex-wrap: wrap; gap: 10px 16px; padding-top: 10px; }
  .basket-btn { margin-left: auto; }
  .nav { order: 1; flex-basis: 100%; margin: 0 -12px; }
  .nav-item { height: 44px; }
}
@media (max-width: 720px) {
  .bar { gap: 10px 12px; }
  .brand :deep(.en), .avatar { display: none; }
  .nav-item { height: 42px; }
}
@media (max-width: 420px) {
  .brand :deep(.text) { display: none; }
}
</style>
