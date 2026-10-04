<script setup lang="ts">
import { ref, watch } from 'vue'
import { parseApi } from '@/api/parse'
import ModalDialog from '@/components/ModalDialog.vue'
import LoadingState from '@/components/LoadingState.vue'

const props = defineProps<{ jobId: string; fileName: string }>()
const open = defineModel<boolean>({ required: true })
const url = ref('')
const loading = ref(false)
const error = ref('')
const attempt = ref(0)

watch([open, () => props.jobId, attempt], async ([isOpen, jobId], _, onCleanup) => {
  url.value = ''
  error.value = ''
  loading.value = false
  if (!isOpen || !jobId) return

  const controller = new AbortController()
  let objectUrl = ''
  onCleanup(() => {
    controller.abort()
    if (objectUrl) URL.revokeObjectURL(objectUrl)
  })
  loading.value = true
  try {
    const pdf = await parseApi.getPdf(jobId, controller.signal)
    if (controller.signal.aborted) return
    objectUrl = URL.createObjectURL(pdf)
    url.value = objectUrl
  } catch (e) {
    if (!controller.signal.aborted) error.value = (e as Error).message
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}, { immediate: true })
</script>

<template>
  <ModalDialog v-model="open" title="原卷 PDF 预览" :width="1100">
    <div class="preview">
      <div class="toolbar">
        <span class="file-name">{{ fileName }}</span>
        <div v-if="url" class="actions">
          <a class="btn" :href="url" target="_blank" rel="noopener">新窗口打开</a>
          <a class="btn" :href="url" :download="fileName">下载 PDF</a>
        </div>
      </div>
      <LoadingState v-if="loading" label="正在加载 PDF…" />
      <div v-else-if="error" class="error" role="alert">
        <span>{{ error }}</span>
        <button class="btn" @click="attempt++">重新加载</button>
      </div>
      <template v-else-if="url">
        <p class="hint">可在预览中翻页、缩放；若浏览器无法显示，请在新窗口打开或下载查看。</p>
        <iframe :src="url" :title="`${fileName} · PDF 预览`" class="pdf-frame" />
      </template>
    </div>
  </ModalDialog>
</template>

<style scoped>
.preview { display: flex; flex-direction: column; gap: 12px; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.file-name { flex: 1; min-width: 0; overflow-wrap: anywhere; font-size: 14px; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
.hint { margin: 0; font-size: 12px; color: var(--c-text-3); }
.pdf-frame { width: 100%; height: min(68vh, 800px); border: 1px solid var(--c-border); border-radius: var(--r-sm); background: var(--c-surface-2); }
.error { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: 20px 0; font-size: 14px; color: var(--c-danger); }
</style>
