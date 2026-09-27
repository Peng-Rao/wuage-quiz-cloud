<script setup lang="ts">
import { ref } from 'vue'
import { request } from '@/api/request'
import { useAppStore } from '@/stores/app'
import { fromBank, useBasketStore } from '@/stores/basket'
import type { BankQuestion } from '@/api/bank'
const app = useAppStore()
const basket = useBasketStore()
const count = ref(10)
const difficulty = ref('适中')
const requirements = ref('')
const busy = ref(false)
const error = ref('')
const message = ref('')
async function compose() {
  busy.value = true; error.value = ''; message.value = ''
  try {
    const result = await request<{ items: BankQuestion[] }>('POST', '/api/papers/compose', { subject: app.subject, stage: app.stage, count: count.value, difficulty: difficulty.value, requirements: requirements.value })
    basket.addMany(result.items.map(fromBank))
    message.value = `已选出 ${result.items.length} 道题并加入试题篮，请检查试卷结构。`
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>
<template>
  <details class="card compose-panel"><summary>AI 辅助组卷 · {{ app.stage }}{{ app.subject }}</summary>
    <form @submit.prevent="compose"><label>题量<input v-model.number="count" type="number" min="1" max="30" required></label><label>难度<select v-model="difficulty"><option>容易</option><option>适中</option><option>较难</option></select></label><label class="requirements">组卷要求<input v-model="requirements" maxlength="500" placeholder="例如：侧重函数，兼顾选择题与解答题"></label><button class="btn btn-primary" :disabled="busy">{{ busy ? 'AI 选题中…' : '生成并加入试题篮' }}</button></form>
    <p>从当前学科题库中选题，已有试题保留。生成后可继续手动调整与下载。</p><p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="message" role="status">{{ message }}</p>
  </details>
</template>
<style scoped>
.compose-panel { flex-basis:100%;width:100%;padding:18px 20px; }summary { cursor:pointer;font-weight:600;color:var(--c-primary); }form { display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin-top:18px; }label { display:flex;flex-direction:column;gap:6px;font-size:12px; }input,select { padding:8px;border:1px solid var(--c-border);border-radius:6px; }input[type=number] { width:80px; }.requirements { flex:1;min-width:200px; }p { font-size:12px;color:var(--c-text-3);margin-bottom:0; }.error { color:#a0301f; }
</style>
