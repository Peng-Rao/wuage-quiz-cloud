<script setup lang="ts">
/**
 * 页面图、题图共用的图片：默认懒加载、异步解码，加载中显示扫光占位，解码完成后淡入，失败时显示占位框。
 * 根元素就是 <img>，父组件 scoped 样式里的 `.images img` 等选择器照常生效。
 *
 * 清晰度：服务端每张图只有一种分辨率（页面按 page_dpi 渲染），没有可供 srcset 选择的多档尺寸；
 * 这里只按图片自身尺寸显示或缩小，从不放大，避免在高分屏上二次插值变糊。
 * 占位比例：父元素可设 `--img-ratio`（如 210 / 297），加载前按该比例占位，图片出来时版面不跳动。
 */
import { onMounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  src: string
  alt: string
  /** 首屏或打印用的图：立即加载 */
  eager?: boolean
}>(), { eager: false })

const state = ref<'loading' | 'loaded' | 'error'>('loading')
const el = ref<HTMLImageElement>()

watch(() => props.src, () => { state.value = 'loading' })

function onLoad() {
  const img = el.value
  // 等解码完再显示，避免大图先出现半张或卡一帧
  if (!img?.decode) { state.value = 'loaded'; return }
  img.decode().catch(() => undefined).finally(() => { state.value = 'loaded' })
}

// 缓存命中时 load 可能在挂载前就已触发
onMounted(() => {
  const img = el.value
  if (img?.complete) state.value = img.naturalWidth ? 'loaded' : 'error'
})
</script>

<template>
  <img
    ref="el" class="lazy-img" :class="`is-${state}`" :src="src" :alt="alt"
    :loading="eager ? 'eager' : 'lazy'" decoding="async"
    @load="onLoad" @error="state = 'error'"
  >
</template>

<style scoped>
.lazy-img.lazy-img.is-loading {
  min-width: 96px; min-height: 64px; aspect-ratio: var(--img-ratio, auto); color: transparent;
  background: linear-gradient(100deg, var(--c-divider) 30%, var(--c-paper-2) 50%, var(--c-divider) 70%) 0 0 / 300% 100%;
  animation: img-shimmer 1.4s ease-in-out infinite;
}
.lazy-img.is-loaded { animation: img-in var(--dur-slow) var(--ease-out); }
.lazy-img.lazy-img.is-error {
  min-width: 120px; min-height: 64px; padding: 8px; border: 1px dashed var(--c-border-strong); border-radius: var(--r-sm);
  background: var(--c-surface-2); color: var(--c-text-4); font-size: 12px;
}
@keyframes img-shimmer { from { background-position: 100% 0; } to { background-position: 0 0; } }
@keyframes img-in { from { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  .lazy-img.lazy-img.is-loading, .lazy-img.is-loaded { animation: none; }
}
@media print {
  .lazy-img.is-loaded { animation: none; }
}
</style>
