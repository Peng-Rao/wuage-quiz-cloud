<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { request } from '@/api/request'
import type { User } from '@/stores/auth'
import type { BankQuestion } from '@/api/bank'
const props = defineProps<{ questions: BankQuestion[] }>()
const emit = defineEmits<{ updated: [] }>()
const users = ref<User[]>([])
const selected = ref<string[]>([])
const ownerId = ref('')
const error = ref('')
const message = ref('')
const busy = ref(false)
watch(() => props.questions, () => { selected.value = [] })
onMounted(async () => {
  try { users.value = await request<User[]>('GET', '/api/review-recipients') }
  catch (e) { error.value = (e as Error).message }
})
async function save(approved: boolean) {
  error.value = ''; message.value = ''; busy.value = true
  try {
    await request('POST', '/api/bank/review', { questionIds: selected.value, ownerId: ownerId.value, approved })
    message.value = approved ? '已审核通过并分配，用户现在可查看这些题目和知识点。' : '已撤回审核，普通用户将无法再查看这些题目。'
    emit('updated')
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>
<template>
  <details class="card review-panel">
    <summary>审核与分配题目</summary>
    <p>勾选已核对的题目，分配给普通用户后，对方才能查看并组卷。</p>
    <button type="button" class="btn-link" @click="selected = selected.length === questions.length ? [] : questions.map(q => q.id)">{{ selected.length === questions.length ? '取消全选' : '全选题目' }}</button>
    <div class="review-list"><label v-for="(q, i) in questions" :key="q.id"><input v-model="selected" type="checkbox" :value="q.id"><span>第 {{ i + 1 }} 题 · {{ q.stem.slice(0, 70) }}</span><small>{{ q.reviewedAt ? '已审核' : '待审核' }}{{ q.ownerId ? ' · ' + (users.find(u => u.id === q.ownerId)?.displayName ?? '已分配') : '' }}</small></label></div>
    <div class="actions"><select v-model="ownerId" aria-label="分配给用户"><option value="">选择题目归属用户</option><option v-for="u in users" :key="u.id" :value="u.id">{{ u.displayName }}（{{ u.username }}）</option></select><button class="btn btn-primary" :disabled="!ownerId || !selected.length || busy" @click="save(true)">审核通过并分配</button><button class="btn" :disabled="!ownerId || !selected.length || busy" @click="save(false)">撤回审核</button></div>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="message" role="status">{{ message }}</p>
  </details>
</template>
<style scoped>
.review-panel { padding:20px; }summary { cursor:pointer;font-weight:600;color:var(--c-primary); }p { font-size:13px;color:var(--c-text-3);line-height:1.7; }.review-list { max-height:280px;overflow:auto;margin:14px 0; }.review-list label { display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--c-divider);font-size:13px; }.review-list span { flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }.review-list small { color:var(--c-text-3); }.actions { display:flex;gap:10px;flex-wrap:wrap; }select { max-width:100%;padding:8px;border:1px solid var(--c-border);border-radius:6px; }.error { color:var(--c-danger); }input { accent-color:var(--c-primary); }
</style>
