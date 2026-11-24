<script setup lang="ts">
import { adminApi } from '@/api/admin'
import CostView from '@/views/CostView.vue'
</script>

<template>
  <!-- 与题库「AI 成本」页相同的统计，数据来自后台接口 -->
  <CostView class="adm-usage" :api="adminApi.usage" badge="管理后台" />
</template>

<style scoped>
.adm-usage { padding-left: 32px; padding-right: 32px; margin: 0; max-width: 1280px; }
@media (max-width: 960px) { .adm-usage { padding-left: 16px; padding-right: 16px; } }
</style>
