<script setup lang="ts">
import AppHeader from './components/AppHeader.vue'
import AppFooter from './components/AppFooter.vue'
</script>

<template>
  <div class="layout">
    <AppHeader />
    <RouterView />
    <AppFooter />
  </div>
</template>

<style scoped>
.layout { min-height: 100vh; display: flex; flex-direction: column; }
.layout > :deep(main) { flex: 1; }
</style>
