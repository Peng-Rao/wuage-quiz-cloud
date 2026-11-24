<script setup lang="ts">
import type { SourceImage } from '@/api/parse'
import ModalDialog from '@/components/ModalDialog.vue'
import LoadingState from '@/components/LoadingState.vue'
import LazyImg from '@/components/LazyImg.vue'
import { ref } from 'vue'

defineProps<{ title: string; images: SourceImage[] | null }>()
const open = defineModel<boolean>({ required: true })
/** 原始大小：按页面图自身像素显示（可横向滚动），比缩放到弹窗宽度更清晰 */
const actual = ref(false)

/** bbox 为归一化坐标，直接换算为百分比定位 */
const boxStyle = (b: [number, number, number, number]) => ({
  left: b[0] * 100 + '%',
  top: b[1] * 100 + '%',
  width: (b[2] - b[0]) * 100 + '%',
  height: (b[3] - b[1]) * 100 + '%',
})
</script>

<template>
  <ModalDialog v-model="open" :title="title" :width="820">
    <LoadingState v-if="!images" label="正在加载原卷图片…" />
    <p v-else-if="!images.length" class="empty">这道题暂无原卷图片，可能是原卷未能转换为页面图。</p>
    <template v-else>
    <div class="view-bar">
      <span>共 {{ images.length }} 页，橙框为本题所在区域</span>
      <button type="button" class="chip" :class="actual ? 'is-solid' : 'is-soft'" :aria-pressed="actual" @click="actual = !actual">
        {{ actual ? '适应宽度' : '原始大小' }}
      </button>
    </div>
    <div class="pages" :class="{ actual }">
      <figure v-for="(img, i) in images" :key="img.page" class="page">
        <div class="sheet">
          <LazyImg :src="img.url" :alt="`原卷第 ${img.page} 页`" :eager="i === 0" />
          <span v-for="(r, i) in img.regions" :key="i" class="box" :style="boxStyle(r.bbox)" />
        </div>
        <figcaption>原卷第 {{ img.page }} 页</figcaption>
      </figure>
    </div>
    </template>
  </ModalDialog>
</template>

<style scoped>
.view-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; font-size: 12px; color: var(--c-text-3); }
.pages { display: flex; flex-direction: column; gap: 16px; }
.page { margin: 0; display: flex; flex-direction: column; gap: 6px; }
/* 加载前按 A4 竖版占位，图片出来时弹窗不跳动 */
.sheet { --img-ratio: 210 / 297; position: relative; border: 1px solid var(--c-border); border-radius: var(--r-sm); overflow: hidden; background: #fff; box-shadow: var(--shadow-sm); }
.sheet img { display: block; width: 100%; height: auto; }
.pages.actual { overflow-x: auto; align-items: flex-start; }
.pages.actual .sheet { width: max-content; max-width: none; }
.pages.actual .sheet img { width: auto; max-width: none; }
.pages.actual figcaption { align-self: stretch; }
.box { position: absolute; border: 2px solid var(--c-primary); background: color-mix(in srgb, var(--c-primary) 10%, transparent); border-radius: 3px; animation: box-pulse 1.6s var(--ease-in-out) 2; }
@keyframes box-pulse { 50% { box-shadow: 0 0 0 6px color-mix(in srgb, var(--c-primary) 22%, transparent); } }
@media (prefers-reduced-motion: reduce) { .box { animation: none; } }
.empty { margin: 0; padding: 32px 0; text-align: center; font-size: 14px; color: var(--c-text-3); }
figcaption { font-size: 12px; color: var(--c-text-3); text-align: center; }
</style>
