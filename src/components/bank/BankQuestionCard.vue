<script setup lang="ts">
import { computed } from 'vue'
import { coefToDiff } from '@/data/mock'
import type { BankQuestion } from '@/api/bank'
import MathText from '@/components/MathText.vue'

const props = defineProps<{
  q: BankQuestion
  /** 试卷详情中显示原卷题号 */
  no?: number
  showAnswer: boolean
  inBasket: boolean
  /** 不显示出处（试卷详情中出处即本卷） */
  hideSource?: boolean
}>()
defineEmits<{ 'toggle-answer': []; 'toggle-basket': [] }>()

const LETTERS = 'ABCDEFGH'
const diff = computed(() => coefToDiff(props.q.coef))
</script>

<template>
  <article class="card question" :class="{ 'in-basket': inBasket }">
    <div class="q-body">
      <div class="q-meta">
        <span class="tag">{{ q.type }}</span>
        <span v-if="no" class="q-score">第 {{ no }} 题 · {{ q.score }} 分</span>
        <span v-if="!hideSource && q.source" class="q-src">{{ q.source.label }}</span>
      </div>
      <div class="q-stem serif"><MathText :text="q.stem" /></div>
      <div v-if="q.images.length" class="q-images">
        <img v-for="src in q.images" :key="src" :src="src" alt="题目配图" loading="lazy">
      </div>
      <div v-if="q.options.length" class="q-options serif">
        <span v-for="(o, i) in q.options" :key="i">{{ LETTERS[i] }}．<MathText :text="o" /></span>
      </div>
    </div>
    <div v-if="showAnswer" class="q-answer">
      <div><b>【答案】</b><MathText v-if="q.answer" :text="q.answer" /><span v-else class="none">暂无</span></div>
      <div v-if="q.analysis"><b>【解析】</b><MathText :text="q.analysis" /></div>
      <div v-if="q.knowledgePoints.length" class="kp">
        <b>【知识点】</b>{{ q.knowledgePoints.map((k) => k.name).join('；') }}
      </div>
    </div>
    <div class="q-foot">
      <span>难度 <b>{{ diff }}</b>（{{ q.coef.toFixed(2) }}）</span>
      <span v-if="!no">原卷 {{ q.score }} 分</span>
      <span v-if="q.answerSource === 'ai'" class="ai">答案由 AI 生成</span>
      <div class="q-actions">
        <button class="btn-link is-primary" @click="$emit('toggle-answer')">{{ showAnswer ? '收起解析' : '查看解析' }}</button>
        <button class="btn-link">收藏</button>
        <button class="btn-link">纠错</button>
        <button class="add-btn" :class="{ added: inBasket }" @click="$emit('toggle-basket')">
          {{ inBasket ? '移出试题篮' : '＋ 加入试题篮' }}
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.question { overflow: hidden; transition: border-color .15s; }
.question.in-basket { border-color: var(--c-primary-line); }
.q-body { padding: 18px 22px 14px; display: flex; flex-direction: column; gap: 12px; }
.q-meta { display: flex; gap: 8px; font-size: 12px; color: var(--c-text-4); align-items: center; min-width: 0; }
.q-score { color: var(--c-text-2); font-weight: 500; flex-shrink: 0; }
.q-src { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.q-stem { font-size: 15.5px; line-height: 1.9; color: var(--c-ink); text-wrap: pretty; }
.q-images { display: flex; flex-wrap: wrap; gap: 10px; }
.q-images img { max-width: min(100%, 320px); max-height: 220px; object-fit: contain; }
.q-options { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 4px 20px; font-size: 15px; line-height: 1.9; }
.q-answer {
  margin: 0 22px 14px; padding: 12px 16px; background: var(--c-paper); border-radius: var(--r-md);
  display: flex; flex-direction: column; gap: 6px; font-size: 14px; line-height: 1.8;
}
.q-answer b { color: var(--c-primary); font-weight: 600; }
.q-answer .kp { color: var(--c-text-3); }
.none { color: var(--c-text-4); }
.q-foot {
  border-top: 1px solid var(--c-divider); padding: 10px 22px; display: flex; flex-wrap: wrap; align-items: center;
  gap: 8px 18px; font-size: 12px; color: var(--c-text-4); background: var(--c-surface-2);
}
.q-foot b { color: var(--c-text-2); font-weight: 500; }
.q-foot .ai { color: #B5661B; }
.q-actions { margin-left: auto; display: flex; gap: 14px; align-items: center; }
.add-btn { border: none; border-radius: var(--r-sm); padding: 6px 14px; font-size: 13px; background: var(--c-primary); color: #fff; }
.add-btn.added { background: var(--c-paper); color: var(--c-text-3); }
</style>
