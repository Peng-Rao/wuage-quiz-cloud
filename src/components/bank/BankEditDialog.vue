<script setup lang="ts">
import { ref, watch } from 'vue'
import { bankApi, type BankQuestion } from '@/api/bank'
import type { DraftQuestionPatch } from '@/api/parse'
import { fromBank, useBasketStore } from '@/stores/basket'
import EditQuestionDialog from '@/components/parse/EditQuestionDialog.vue'

/** 管理员修改已入库的题：保存后更新试题篮中的快照 */
const props = defineProps<{ q: BankQuestion | null; no?: number }>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ saved: [q: BankQuestion] }>()

const basket = useBasketStore()
const saving = ref(false)
const error = ref('')
watch(open, () => { error.value = '' })

async function save(patch: DraftQuestionPatch) {
  if (!props.q) return
  saving.value = true
  error.value = ''
  try {
    const q = await bankApi.updateQuestion(props.q.id, patch)
    if (basket.has(q.id)) basket.refresh(fromBank(q))
    emit('saved', q)
    open.value = false
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <EditQuestionDialog
    v-model="open" :q="q" :job-id="q?.paperId ?? ''" :saving="saving" :error="error"
    :title="no ? `编辑第 ${no} 题` : '编辑题目'"
    tip="修改直接写入题库并同步到原卷解析结果，审核状态与归属不变。"
    @save="save"
  />
</template>
