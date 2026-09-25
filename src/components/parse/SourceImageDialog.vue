<script setup lang="ts">
import type { SourceImage } from '@/api/parse'
import ModalDialog from '@/components/ModalDialog.vue'

defineProps<{ title: string; images: SourceImage[] | null }>()
const open = defineModel<boolean>({ required: true })

/** bbox 为归一化坐标，直接换算为百分比定位 */
const boxStyle = (b: [number, number, number, number]) => ({
  left: b[0] * 100 + '%',
  top: b[1] * 100 + '%',
  width: (b[2] - b[0]) * 100 + '%',
  height: (b[3] - b[1]) * 100 + '%',
})
</script>

<template>
  <ModalDialog v-model="open" :title="title" :width="620">
    <p v-if="!images" class="muted">加载中…</p>
    <div v-else class="pages">
      <figure v-for="img in images" :key="img.page" class="page">
        <div class="sheet">
          <img :src="img.url" :alt="`原卷第 ${img.page} 页`">
          <span v-for="(r, i) in img.regions" :key="i" class="box" :style="boxStyle(r.bbox)" />
        </div>
        <figcaption>原卷第 {{ img.page }} 页</figcaption>
      </figure>
    </div>
  </ModalDialog>
</template>

<style scoped>
.pages { display: flex; flex-direction: column; gap: 16px; }
.page { margin: 0; display: flex; flex-direction: column; gap: 6px; }
.sheet { position: relative; border: 1px solid var(--c-border); border-radius: var(--r-sm); overflow: hidden; background: #fff; }
.sheet img { display: block; width: 100%; height: auto; }
.box { position: absolute; border: 2px solid var(--c-primary); background: rgba(184, 86, 31, .1); border-radius: 3px; }
figcaption { font-size: 12px; color: var(--c-text-3); text-align: center; }
</style>
