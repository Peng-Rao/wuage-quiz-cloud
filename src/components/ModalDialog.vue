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
  border-radius: var(--r-lg); box-shadow: 0 20px 48px rgba(27, 36, 48, .22); color: var(--c-ink);
}
.modal::backdrop { background: rgba(27, 36, 48, .45); }
.inner { display: flex; flex-direction: column; max-height: calc(100vh - 48px); }
.head { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--c-divider); }
.close { border: none; background: transparent; font-size: 22px; line-height: 1; color: var(--c-text-3); padding: 0 4px; }
.close:hover { color: var(--c-ink); }
.body { padding: 18px 20px; overflow: auto; }
.foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 20px; border-top: 1px solid var(--c-divider); background: var(--c-surface-2); }
</style>
