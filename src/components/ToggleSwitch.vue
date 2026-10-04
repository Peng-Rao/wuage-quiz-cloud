<script setup lang="ts">
defineProps<{ label: string; description?: string }>()
const on = defineModel<boolean>({ required: true })
</script>

<template>
  <label class="row">
    <span class="copy"><span class="label">{{ label }}</span><span v-if="description" class="description">{{ description }}</span></span>
    <button type="button" role="switch" :aria-label="label" :aria-checked="on" class="track" :class="{ on }" @click="on = !on">
      <span class="knob" />
    </button>
  </label>
</template>

<style scoped>
.row { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 13px; cursor: pointer; }
.label { color: var(--c-text-2); }
.copy { display: flex; flex-direction: column; gap: 4px; }
.description { color: var(--c-text-3); font-size: 12px; line-height: 1.5; }
.track:focus-visible { outline: 3px solid var(--c-primary-line); outline-offset: 3px; }
.track {
  flex-shrink: 0; width: 34px; height: 20px; border-radius: 10px; border: none; padding: 0;
  background: var(--c-toggle-off); position: relative; transition: background .15s;
}
.track.on { background: var(--c-primary); }
.knob {
  position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 8px;
  background: var(--c-surface); box-shadow: 0 1px 2px rgba(0, 0, 0, .2); transition: left .15s;
}
.track.on .knob { left: 16px; }
</style>
