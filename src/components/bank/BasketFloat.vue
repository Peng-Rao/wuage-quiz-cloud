<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useBasketStore } from '@/stores/basket'

/** 选题页右侧贴边的试题篮：收起时只占一条竖向标签，点开显示各题型题数与分值 */
const basket = useBasketStore()
const open = ref(false)
const root = ref<HTMLElement>()

function onDocClick(e: MouseEvent) {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
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
  <aside ref="root" class="bf no-print" aria-label="试题篮">
    <div v-if="open" class="panel">
      <div class="panel-head">
        <span class="title">试题篮</span>
        <span class="sum">{{ basket.count }} 题 · {{ basket.totalScore }} 分</span>
      </div>
      <div v-if="basket.count" class="types">
        <div v-for="s in basket.sections" :key="s.type" class="type-row">
          <span>{{ s.type }}</span><span><b>{{ s.items.length }}</b> 题 · {{ s.score }} 分</span>
        </div>
      </div>
      <p v-else class="empty">还没有选题，点击题目下方的「加入试题篮」。</p>
      <RouterLink to="/paper" class="go">去组卷</RouterLink>
      <button v-if="basket.count" class="clear" @click="basket.clear()">清空试题篮</button>
    </div>
    <button class="tab" :aria-expanded="open" @click="open = !open">
      <span class="tab-text">试题篮</span>
      <span class="badge">{{ basket.count }}</span>
    </button>
  </aside>
</template>

<style scoped>
.bf { position: fixed; right: 0; top: 50%; transform: translateY(-50%); z-index: 15; display: flex; align-items: center; }
.tab {
  width: 36px; border: none; border-radius: var(--r-md) 0 0 var(--r-md); background: var(--c-primary); color: #fff;
  padding: 12px 0 10px; display: flex; flex-direction: column; align-items: center; gap: 8px;
  box-shadow: -4px 6px 18px rgb(var(--shadow-rgb) / .16); font-size: 14px; font-weight: 600;
}
.tab:hover { background: var(--c-primary-dark); }
.tab-text { writing-mode: vertical-rl; letter-spacing: 4px; }
.badge {
  min-width: 22px; height: 20px; border-radius: 10px; background: var(--c-surface); color: var(--c-primary); font-size: 12px;
  display: inline-flex; align-items: center; justify-content: center; padding: 0 5px;
}
.panel {
  width: 230px; margin-right: 8px; background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--r-lg);
  box-shadow: 0 16px 40px rgb(var(--shadow-rgb) / .16); padding: 14px; display: flex; flex-direction: column; gap: 10px;
}
.panel-head { display: flex; align-items: baseline; justify-content: space-between; }
.title { font-size: 15px; font-weight: 600; }
.sum { font-size: 13px; color: var(--c-primary); font-weight: 600; }
.types { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid var(--c-divider); padding-top: 10px; }
.type-row { display: flex; justify-content: space-between; font-size: 13px; color: var(--c-text-2); }
.type-row b { color: var(--c-ink); }
.empty { margin: 0; font-size: 12px; color: var(--c-text-4); line-height: 1.6; }
.go {
  display: flex; align-items: center; justify-content: center; height: 34px; border-radius: var(--r-sm);
  background: var(--c-primary); color: #fff; font-size: 14px;
}
.go:hover { text-decoration: none; color: #fff; background: var(--c-primary-dark); }
.clear { border: none; background: none; font-size: 12px; color: var(--c-text-4); padding: 0; }
.clear:hover { color: var(--c-hard); }
</style>
