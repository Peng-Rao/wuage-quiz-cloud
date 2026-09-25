<template>
  <RouterLink to="/" class="brand" aria-label="福格云上题库 首页">
    <svg viewBox="0 0 450 365" class="mark" fill="var(--c-logo)" aria-hidden="true">
      <rect x="0" y="0" width="87" height="365" />
      <rect x="0" y="0" width="450" height="95" />
      <path d="M175,133 H442 V365 H162 V283 H300 A52.5 52.5 0 0 0 300,178 H175 A22.5 22.5 0 0 1 175,133 Z" />
    </svg>
    <span class="text">
      <span class="name">福格云上题库</span>
      <span class="en">FG Education</span>
    </span>
  </RouterLink>
</template>

<style scoped>
.brand { display: flex; align-items: center; gap: 10px; flex-shrink: 0; white-space: nowrap; text-decoration: none; }
.mark { width: 34px; height: 28px; flex-shrink: 0; display: block; }
.text { display: flex; flex-direction: column; line-height: 1.15; gap: 1px; }
.name { font-weight: 700; font-size: 17px; letter-spacing: 1px; color: var(--c-primary); }
.en { font-size: 10px; letter-spacing: .5px; color: var(--c-text-4); font-weight: 500; }
</style>
