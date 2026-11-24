<script setup lang="ts">
import AppHeader from './components/AppHeader.vue'
import AppFooter from './components/AppFooter.vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const route = useRoute()
</script>

<template>
  <!-- 管理后台有自己的布局，不显示题库的页头页脚 -->
  <RouterView v-if="route.meta.console" />
  <div v-else class="layout">
    <AppHeader v-if="auth.user" />
    <RouterView />
    <AppFooter />
  </div>
</template>

<style scoped>
.layout { min-height: 100vh; display: flex; flex-direction: column; }
.layout > :deep(main) { flex: 1; }
/* 切换页面时新页面淡入。只动 opacity：不留 transform，页面里的 fixed / sticky 元素位置不受影响 */
.layout > :deep(main) { animation: fade-in var(--dur-slow) var(--ease-out) backwards; }
@media (prefers-reduced-motion: reduce), print {
  .layout > :deep(main) { animation: none; }
}
</style>
