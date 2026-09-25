<script setup lang="ts">
import { computed, ref } from 'vue'
import type { DraftQuestion } from '@/api/parse'
import { coefToDiff, type Difficulty } from '@/data/mock'

const props = defineProps<{
  q: DraftQuestion
  selected: boolean
  busy: boolean
  low: boolean
  isFirst: boolean
}>()
defineEmits<{
  toggle: []
  cycleType: []
  cycleDiff: []
  edit: []
  merge: []
  split: []
  source: []
}>()

const D_CLASS: Record<Difficulty, string> = { 容易: 'easy', 适中: 'mid', 较难: 'hard' }
const LETTERS = 'ABCDEFGH'

const diff = computed(() => coefToDiff(props.q.coef))
const filled = computed(() => Math.max(1, Math.round((1 - props.q.coef) * 5)))
const hasAnswer = computed(() => !!props.q.answer)
const showAnswer = ref(false)
</script>

<template>
  <article class="card pq" :class="{ low, busy }">
    <div class="pq-body">
      <button
        class="check" :class="{ on: selected }" role="checkbox" :aria-checked="selected"
        :aria-label="`选择第 ${q.no} 题`" @click="$emit('toggle')"
      >{{ selected ? '✓' : '' }}</button>
      <div class="pq-main">
        <div class="pq-meta">
          <span class="pq-no">第 {{ q.no }} 题</span>
          <button class="type-btn" title="点击切换题型" :disabled="busy" @click="$emit('cycleType')">{{ q.type }} ▾</button>
          <span class="muted-2">{{ q.score }} 分 · 原卷第 {{ q.page }} 页</span>
          <span v-if="low" class="low-tag">待核对 · 置信度 {{ Math.round(q.confidence * 100) }}%</span>
          <span v-if="q.duplicateOf" class="dup-tag" :title="`题库题目 ${q.duplicateOf}`">题库已有相似题</span>
          <span v-if="q.status === 'saved'" class="saved-tag">已入库</span>
        </div>
        <div class="pq-stem serif">{{ q.stem }}</div>
        <div v-if="q.options.length" class="opts serif">
          <span v-for="(o, i) in q.options" :key="i"><b>{{ LETTERS[i] }}．</b>{{ o }}</span>
        </div>
        <div v-if="showAnswer && hasAnswer" class="ans">
          <p><b>【答案】</b>{{ q.answer }}</p>
          <p v-if="q.analysis"><b>【解析】</b>{{ q.analysis }}</p>
        </div>
        <div v-if="q.knowledgePoints.length" class="kps">
          <span v-for="k in q.knowledgePoints" :key="k.id">{{ k.name }}</span>
        </div>
      </div>
      <div class="pq-diff" :class="D_CLASS[diff]">
        <span class="muted-2 small">难度评估</span>
        <div class="coef"><b>{{ q.coef.toFixed(2) }}</b><span>{{ diff }}</span></div>
        <div class="bars">
          <span v-for="i in 5" :key="i" :class="{ filled: i <= filled }" />
        </div>
        <button class="btn-link is-primary small" :disabled="busy" @click="$emit('cycleDiff')">调整难度</button>
      </div>
    </div>
    <div class="pq-foot">
      <button class="btn-link" :disabled="busy" @click="$emit('edit')">编辑题目</button>
      <button class="btn-link" :disabled="busy || isFirst" @click="$emit('merge')">与上题合并</button>
      <button class="btn-link" :disabled="busy" @click="$emit('split')">拆分小问</button>
      <button class="btn-link" :disabled="busy" @click="$emit('source')">查看原图</button>
      <button v-if="hasAnswer" class="btn-link ans-note" @click="showAnswer = !showAnswer">
        已关联答案解析 · {{ showAnswer ? '收起' : '查看' }}
      </button>
      <span v-else class="ans-note missing">未识别到答案</span>
    </div>
  </article>
</template>

<style scoped>
.small { font-size: 12px; }
.muted-2 { color: var(--c-text-3); }
.pq { overflow: hidden; transition: opacity .15s; }
.pq.low { border-color: #EFC2B8; }
.pq.busy { opacity: .6; }
.pq-body { padding: 16px 20px; display: flex; flex-wrap: wrap; gap: 14px; }
.check {
  flex-shrink: 0; width: 20px; height: 20px; margin-top: 3px; border-radius: 5px; border: 1.5px solid #C9C5BA;
  background: #fff; color: #fff; font-size: 12px; line-height: 1; padding: 0;
}
.check.on { background: var(--c-primary); border-color: var(--c-primary); }
.pq-main { flex: 1 1 240px; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
.pq-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px; }
.pq-no { font-weight: 700; color: var(--c-ink); font-size: 13px; }
.type-btn { border: 1px dashed var(--c-primary-line); color: var(--c-primary-dark); background: var(--c-primary-soft); border-radius: 4px; padding: 1px 6px; font-size: 12px; }
.low-tag { color: #A0301F; background: #FBEAE6; border-radius: 4px; padding: 1px 6px; }
.dup-tag { color: #6B4E0F; background: #F8EFD9; border-radius: 4px; padding: 1px 6px; cursor: help; }
.saved-tag { color: #3F7340; background: #E9F1E7; border-radius: 4px; padding: 1px 6px; }
.pq-stem { font-size: 15px; line-height: 1.85; color: var(--c-ink); text-wrap: pretty; white-space: pre-line; }
.opts { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 4px 16px; font-size: 14.5px; line-height: 1.7; }
.opts b { font-weight: 400; }
.ans { background: var(--c-surface-2); border: 1px solid var(--c-divider); border-radius: var(--r-md); padding: 10px 12px; font-size: 13.5px; line-height: 1.75; }
.ans p { margin: 0; }
.ans b { color: var(--c-primary-dark); font-weight: 600; }
.kps { display: flex; flex-wrap: wrap; gap: 6px; }
.kps span { font-size: 12px; color: var(--c-text-2); background: var(--c-paper); border-radius: 4px; padding: 2px 8px; }

.pq-diff { flex: 0 0 140px; display: flex; flex-direction: column; gap: 6px; border-left: 1px solid var(--c-divider); padding-left: 16px; }
.pq-diff.easy { --d-text: #3F7340; --d-bar: var(--c-easy); }
.pq-diff.mid { --d-text: #A3620F; --d-bar: var(--c-mid); }
.pq-diff.hard { --d-text: #A0301F; --d-bar: var(--c-hard); }
.coef { display: flex; align-items: baseline; gap: 6px; color: var(--d-text); font-size: 13px; }
.coef b { font-size: 20px; font-weight: 700; }
.bars { display: flex; gap: 2px; }
.bars span { flex: 1; height: 5px; border-radius: 2px; background: var(--c-divider); }
.bars span.filled { background: var(--d-bar); }
.pq-diff .btn-link { align-self: flex-start; padding: 2px 0; }

.pq-foot {
  border-top: 1px solid var(--c-divider); padding: 8px 20px; display: flex; flex-wrap: wrap; gap: 8px 16px;
  font-size: 12px; background: var(--c-surface-2); color: var(--c-text-3);
}
.pq-foot .btn-link { font-size: 12px; color: var(--c-text-2); }
.pq-foot .btn-link:hover:not(:disabled) { color: var(--c-primary); }
.pq-foot .btn-link:disabled { color: var(--c-text-4); cursor: not-allowed; }
.ans-note { margin-left: auto; }
.pq-foot .ans-note.btn-link { color: var(--c-text-3); }
.ans-note.missing { color: #A0301F; }

@media (max-width: 800px) {
  .pq-diff { flex: 1 1 100%; border-left: none; padding-left: 0; border-top: 1px solid var(--c-divider); padding-top: 10px; }
}
</style>
