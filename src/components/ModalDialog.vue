<script setup lang="ts">
import { ref, watch } from 'vue'

const props = withDefaults(defineProps<{ title: string; width?: number }>(), { width: 640 })
const open = defineModel<boolean>({ required: true })
const el = ref<HTMLDialogElement>()

watch([open, el], ([v, d]) => {
  if (!d) return
  if (v && !d.open) d.showModal()
  else if (!v && d.open) d.close()
}, { immediate: true })

function onBackdrop(e: MouseEvent) {
  if (e.target === el.value) open.value = false
}
</script>

<template>
  <dialog ref="el" class="modal" :style="{ width: props.width + 'px' }" @close="open = false" @click="onBackdrop">
    <div v-if="open" class="inner">
      <header class="head">
        <span class="card-title">{{ title }}</span>
        <button type="button" class="close" aria-label="关闭" @click="open = false">×</button>
      </header>
      <div class="body"><slot /></div>
      <footer v-if="$slots.footer" class="foot"><slot name="footer" /></footer>
    </div>
  </dialog>
</template>

<style scoped>
.modal {
  max-width: calc(100vw - 32px); max-height: calc(100vh - 48px); padding: 0; border: none;
  border-radius: var(--r-lg); box-shadow: var(--shadow-xl); color: var(--c-ink); background: var(--c-surface);
}
/* 打开时弹窗轻微上浮放大、遮罩淡入（关闭仍即时，不拖慢操作） */
.modal[open] { animation: modal-in var(--dur-slow) var(--ease-out); }
.modal::backdrop { background: rgb(var(--shadow-rgb) / .45); backdrop-filter: blur(2px); }
.modal[open]::backdrop { animation: backdrop-in var(--dur) ease-out; }
@keyframes modal-in { from { opacity: 0; transform: translateY(8px) scale(.98); } }
@keyframes backdrop-in { from { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  .modal[open], .modal[open]::backdrop { animation: none; }
}
.inner { display: flex; flex-direction: column; max-height: calc(100vh - 48px); }
.head { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--c-divider); }
.close { width: 30px; height: 30px; display: grid; place-items: center; border: none; border-radius: var(--r-sm); background: transparent; font-size: 22px; line-height: 1; color: var(--c-text-3); padding: 0; transition: background-color var(--dur-fast) ease, color var(--dur-fast) ease; }
.close:hover { color: var(--c-ink); background: var(--c-paper-2); }
.body { padding: 18px 20px; overflow: auto; }
.foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 20px; border-top: 1px solid var(--c-divider); background: var(--c-surface-2); }
</style>
