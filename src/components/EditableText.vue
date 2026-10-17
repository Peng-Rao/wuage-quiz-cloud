<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

/**
 * 卷面上可直接点击修改的纯文本。输入时只向外同步文字、不回写本元素，光标不会跳动；
 * readonly 时只显示（排版测量用的副本）。为空时显示占位文字，打印时占位不显示但保留高度，分页结果不变。
 */
const props = defineProps<{ modelValue: string; placeholder?: string; multiline?: boolean; readonly?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const el = ref<HTMLElement | null>(null)

const read = () => (el.value?.innerText ?? '').replace(/ /g, ' ')
function sync() {
  if (el.value && (props.readonly || document.activeElement !== el.value) && read() !== props.modelValue) el.value.textContent = props.modelValue
}
onMounted(sync)
watch(() => props.modelValue, sync)

function onInput() {
  emit('update:modelValue', props.multiline ? read() : read().replace(/\n/g, ''))
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' || (e.key === 'Enter' && !props.multiline)) {
    e.preventDefault()
    el.value?.blur()
  }
}
</script>

<template>
  <span
    ref="el" class="editable" :class="{ multiline, on: !readonly }" :data-placeholder="placeholder"
    :contenteditable="readonly ? undefined : 'plaintext-only'" :spellcheck="false"
    :title="readonly ? undefined : '点击修改'" @input="onInput" @keydown="onKeydown" @blur="sync"
  />
</template>

<style scoped>
.editable { display: inline-block; min-width: 1em; border-radius: 3px; outline: 1px dashed transparent; outline-offset: 2px; }
.editable.multiline { white-space: pre-wrap; }
.editable:empty::before { content: attr(data-placeholder); color: var(--c-text-4); }
.editable.on { cursor: text; transition: outline-color .15s, background .15s; }
.editable.on:hover { outline-color: var(--c-border-strong); }
.editable.on:focus { outline: 1px solid var(--c-primary); background: var(--c-primary-soft); }
@media print {
  .editable.on:hover, .editable.on:focus { outline-color: transparent; background: none; }
  .editable:empty::before { color: transparent; }
}
</style>
