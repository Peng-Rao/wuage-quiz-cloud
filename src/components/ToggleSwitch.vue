<script setup lang="ts">
defineProps<{ label: string }>()
const on = defineModel<boolean>({ required: true })
</script>

<template>
  <label class="row">
    <span class="label">{{ label }}</span>
    <button type="button" role="switch" :aria-checked="on" class="track" :class="{ on }" @click="on = !on">
      <span class="knob" />
    </button>
  </label>
</template>

<style scoped>
.row { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 13px; cursor: pointer; }
.label { color: var(--c-text-2); }
.track {
  flex-shrink: 0; width: 34px; height: 20px; border-radius: 10px; border: none; padding: 0;
  background: var(--c-toggle-off); position: relative; transition: background .15s;
}
.track.on { background: var(--c-primary); }
.knob {
  position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 8px;
  background: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, .2); transition: left .15s;
}
.track.on .knob { left: 16px; }
</style>
