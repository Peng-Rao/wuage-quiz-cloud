<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'
import BrandLogo from './BrandLogo.vue'
import { STAGES } from '@/data/mock'
import { useAppStore } from '@/stores/app'
import { useBasketStore } from '@/stores/basket'

const app = useAppStore()
const { stage, subject } = storeToRefs(app)
const basket = useBasketStore()
const route = useRoute()
// 子页面（如 /upload/knowledge）也高亮所属菜单；首页只在根路径高亮
const isActive = (to: string) => (to === '/' ? route.path === '/' : route.path === to || route.path.startsWith(to + '/'))

const NAV = [
  { to: '/', label: '首页' },
  { to: '/pick', label: '选题组卷' },
  { to: '/paper', label: '试卷编辑' },
  { to: '/upload', label: '试卷解析' },
]

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
          <div v-for="(subs, name) in STAGES" :key="name" class="picker-row">
            <span class="picker-stage">{{ name }}</span>
            <div class="picker-subs">
              <button
                v-for="s in subs" :key="s" class="chip picker-sub"
                :class="{ 'is-solid': name === stage && s === subject }"
                @click="pick(name, s)"
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
      <div class="avatar" title="王老师">王</div>
    </div>
  </header>
</template>

<style scoped>
.header { position: sticky; top: 0; z-index: 20; background: #fff; border-bottom: 1px solid var(--c-border); }
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
  position: absolute; top: 42px; left: 0; width: 420px; max-width: calc(100vw - 32px); background: #fff;
  border: 1px solid var(--c-border); border-radius: var(--r-lg); box-shadow: 0 12px 32px rgba(27, 36, 48, .12);
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
  background: #fff; color: var(--c-primary); border-radius: var(--r-md); font-size: 14px;
  display: flex; align-items: center; gap: 8px;
}
.basket-btn:hover { text-decoration: none; background: var(--c-primary-soft); }
.badge {
  min-width: 20px; height: 20px; border-radius: 10px; background: var(--c-ink); color: #fff; font-size: 12px;
  display: flex; align-items: center; justify-content: center; padding: 0 6px;
}
.avatar {
  width: 32px; height: 32px; border-radius: 16px; background: var(--c-primary-soft); color: var(--c-primary);
  display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 600; flex-shrink: 0;
}

@media (max-width: 720px) {
  .bar { height: auto; flex-wrap: wrap; gap: 10px 12px; padding-top: 10px; }
  .brand :deep(.en), .avatar { display: none; }
  .basket-btn { margin-left: auto; }
  .nav { order: 1; flex-basis: 100%; margin: 0 -12px; }
  .nav-item { height: 42px; }
}
@media (max-width: 420px) {
  .brand :deep(.text) { display: none; }
}
</style>
