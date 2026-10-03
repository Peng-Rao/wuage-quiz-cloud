<script setup lang="ts">
import type { SimilarQuestion } from '@/api/parse'
import ModalDialog from '@/components/ModalDialog.vue'
import MathText from '@/components/MathText.vue'
import LoadingState from '@/components/LoadingState.vue'
import { useAppStore } from '@/stores/app'
import { layoutOf, optionCols, optionLabel } from '@/utils/subject'

defineProps<{ title: string; items: SimilarQuestion[] | null; error?: string }>()
const open = defineModel<boolean>({ required: true })

const app = useAppStore()
/** 相似题可能来自不同试卷，逐题按出处学科排版 */
const subj = (s: SimilarQuestion) => s.origin?.subject || app.subject
const pct = (v: number) => Math.round(v * 100) + '%'
</script>

<template>
  <ModalDialog v-model="open" :title="title" :width="680">
    <p v-if="error" class="err">{{ error }}</p>
    <LoadingState v-else-if="!items" label="正在查找相似题…" detail="正在比对题干与知识点" :rows="2" />
    <p v-else-if="!items.length" class="muted">校本题库和其他试卷中没有找到相似的题。</p>
    <ol v-else class="list">
      <li v-for="s in items" :key="s.id" class="item">
        <div class="head">
          <span class="score" :class="{ dup: s.duplicate }">相似度 {{ pct(s.score) }}</span>
          <span v-if="s.duplicate" class="tag dup">疑似重复</span>
          <span class="tag">{{ s.source === 'bank' ? '校本题库' : '其他试卷（未入库）' }}</span>
          <span class="tag plain">{{ s.type }}</span>
        </div>
        <div v-if="s.origin?.label || s.fileName" class="from" :title="s.origin?.fileName || s.fileName || ''">
          来源：{{ s.origin?.label || s.fileName }}
        </div>
        <div class="stem serif" :class="`lay-${layoutOf(subj(s))}`"><MathText :text="s.stem" :subject="subj(s)" /></div>
        <div
          v-if="s.options.length" class="opts serif"
          :class="[`lay-${layoutOf(subj(s))}`, layoutOf(subj(s)) !== 'plain' && ['opt-fixed', `cols-${optionCols(s.options)}`]]"
        >
          <span v-for="(o, i) in s.options" :key="i"><b>{{ optionLabel(layoutOf(subj(s)), i) }}</b><MathText :text="o" :subject="subj(s)" /></span>
        </div>
        <p v-if="s.answer" class="ans"><b>【答案】</b><MathText :text="s.answer" :subject="subj(s)" /></p>
        <div v-if="s.knowledgePoints.length" class="kps">
          <span v-for="k in s.knowledgePoints" :key="k.id">{{ k.name }}</span>
        </div>
        <p class="why">字面 {{ pct(s.lexical) }}<template v-if="s.semantic != null"> · 语义 {{ pct(s.semantic) }}</template></p>
      </li>
    </ol>
    <p class="muted note">相似度综合了文字重合度<template v-if="items?.some((s) => s.semantic != null)">与语义</template>，仅供参考；「疑似重复」建议核对后再决定是否入库。</p>
  </ModalDialog>
</template>

<style scoped>
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.item { border: 1px solid var(--c-border); border-radius: var(--r-md); padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
.head { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; font-size: 12px; }
.score { font-weight: 700; color: var(--c-ink); font-size: 13px; font-variant-numeric: tabular-nums; }
.score.dup { color: #A0301F; }
.tag { color: var(--c-primary-dark); background: var(--c-primary-soft); border-radius: 4px; padding: 1px 6px; }
.tag.dup { color: #A0301F; background: #FBEAE6; }
.tag.plain { color: var(--c-text-2); background: var(--c-paper); }
.from { font-size: 12px; color: var(--c-text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kps { display: flex; flex-wrap: wrap; gap: 6px; }
.kps span { font-size: 12px; color: var(--c-text-2); background: var(--c-paper); border-radius: 4px; padding: 1px 8px; }
.stem { font-size: 14.5px; line-height: 1.8; }
.opts { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 2px 14px; font-size: 14px; }
.opts b { font-weight: 400; }
.ans { margin: 0; font-size: 13px; color: var(--c-text-2); }
.ans b { color: var(--c-primary-dark); font-weight: 600; }
.why { margin: 0; font-size: 11.5px; color: var(--c-text-4); }
.note { font-size: 12px; margin: 14px 0 0; }
.err { color: #A0301F; }
</style>
