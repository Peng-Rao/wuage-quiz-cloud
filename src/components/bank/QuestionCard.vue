<script setup lang="ts">
import { computed, ref } from 'vue'
import { coefToDiff } from '@/data/mock'
import type { BankQuestion } from '@/api/bank'
import { dateLabel, shortSource } from '@/utils/source'
import { plainText } from '@/utils/math'
import { noSep, optionCols, optionLabel, provideSubject } from '@/utils/subject'
import { useAppStore } from '@/stores/app'
import MathText from '@/components/MathText.vue'
import MaterialText from '@/components/MaterialText.vue'

/** 选题结果中的一道题：来源简写、题型 | 难度 | 知识点、题干，底部为来源与操作 */
const props = defineProps<{
  canSimilar?: boolean
  /** 显示「编辑」（仅管理员） */
  canEdit?: boolean
  q: BankQuestion
  /** 列表中的序号 */
  no: number
  showAnswer: boolean
  inBasket: boolean
  /** 试卷详情中不显示来源（即本卷），序号后显示分值 */
  inPaper?: boolean
}>()
defineEmits<{ 'toggle-answer': []; 'toggle-basket': []; similar: []; edit: [] }>()

const app = useAppStore()
const layout = provideSubject(() => props.q.source?.subject || app.subject)
const cols = computed(() => optionCols(props.q.options))
/** 语文、英语的长篇阅读材料在列表中先折叠，展开后看全文 */
const foldable = computed(() => layout.value !== 'plain' && plainText(props.q.stem).length > (layout.value === 'en' ? 700 : 360))
const unfolded = ref(false)
const diff = computed(() => coefToDiff(props.q.coef))
const tag = computed(() => shortSource(props.q.source))
const paperTitle = computed(() => props.q.source?.title || props.q.source?.fileName.replace(/\.[^.]+$/, '') || '')
</script>

<template>
  <article class="card qc" :class="{ 'in-basket': inBasket }">
    <div v-if="tag && !inPaper" class="qc-tag">{{ tag }}</div>
    <div class="qc-meta">
      <span>{{ q.type }}</span>
      <span>{{ diff }}（{{ q.coef.toFixed(2) }}）</span>
      <span v-if="inPaper">{{ q.score }} 分</span>
      <span v-if="q.knowledgePoints.length" class="kps">
        <span v-for="k in q.knowledgePoints" :key="k.id" :title="k.path ?? k.name">{{ k.name }}</span>
      </span>
    </div>
    <div class="qc-body serif" :class="`lay-${layout}`">
      <MaterialText v-if="q.material" :text="q.material" />
      <div class="stem" :class="{ folded: foldable && !unfolded }"><span class="no">{{ no }}{{ noSep(layout) }}</span><MathText :text="q.stem" /></div>
      <button v-if="foldable" class="fold" @click="unfolded = !unfolded">{{ unfolded ? '收起材料 ▴' : '展开全文 ▾' }}</button>
      <div v-if="q.images.length" class="images">
        <img v-for="src in q.images" :key="src" :src="src" alt="题目配图" loading="lazy">
      </div>
      <div v-if="q.options.length" class="options" :class="layout !== 'plain' && ['opt-fixed', `cols-${cols}`]">
        <span v-for="(o, i) in q.options" :key="i"><b>{{ optionLabel(layout, i) }}</b><MathText :text="o" /></span>
      </div>
    </div>
    <div v-if="showAnswer" class="qc-answer">
      <div><b>【答案】</b><MathText v-if="q.answer" :text="q.answer" /><span v-else class="none">暂无</span></div>
      <div v-if="q.analysis"><b>【解析】</b><MathText :text="q.analysis" /></div>
      <div v-if="q.answerSource === 'ai'" class="ai">答案由 AI 生成，请核对后使用<template v-if="q.answerNote">（{{ q.answerNote }}）</template></div>
    </div>
    <div class="qc-foot">
      <span>{{ dateLabel(q.createdAt) }}</span>
      <template v-if="!inPaper && paperTitle">
        <span class="sep">|</span>
        <RouterLink :to="`/papers/${q.paperId}`" class="from" :title="q.source?.label">
          来源：{{ paperTitle }}<template v-if="q.source?.no"> 第 {{ q.source.no }} 题</template>
        </RouterLink>
      </template>
      <div class="acts">
        <button v-if="canSimilar" class="act" @click="$emit('similar')">相似题</button>
        <button v-if="canEdit" class="act" @click="$emit('edit')">编辑</button>
        <button class="act">纠错</button>
        <button class="act" :class="{ on: showAnswer }" @click="$emit('toggle-answer')">{{ showAnswer ? '收起' : '详情' }}</button>
        <button class="act">收藏</button>
        <button class="add" :class="{ added: inBasket }" @click="$emit('toggle-basket')">
          {{ inBasket ? '移出试题篮' : '加入试题篮' }}
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.qc { overflow: hidden; transition: border-color .15s; }
.qc.in-basket { border-color: var(--c-primary-line); }
.qc-tag {
  display: inline-block; margin: 0; padding: 6px 16px; font-size: 13px; color: var(--c-primary);
  background: var(--c-primary-soft); border-bottom-right-radius: var(--r-md);
}
.qc-meta {
  display: flex; flex-wrap: wrap; align-items: center; padding: 10px 22px; gap: 4px 0;
  font-size: 13px; color: var(--c-text-3); border-bottom: 1px solid var(--c-divider);
}
.qc-meta > span:not(:last-child)::after { content: '|'; margin: 0 12px; color: var(--c-border); }
.kps { display: inline-flex; flex-wrap: wrap; gap: 4px 14px; color: var(--c-text-4); }
.qc-body { padding: 16px 22px 14px; display: flex; flex-direction: column; gap: 10px; font-size: 15.5px; line-height: 1.9; color: var(--c-ink); }
.stem { text-wrap: pretty; }
.no { font-family: var(--font-sans); }
.images { display: flex; flex-wrap: wrap; gap: 10px; }
.images img { max-width: min(100%, 320px); max-height: 220px; object-fit: contain; }
.options { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 2px 20px; padding-left: 1.4em; }
.options b { font-weight: 400; }
.lay-en { font-size: 16px; }
.lay-en .no { font-family: inherit; }
.stem.folded { max-height: 15em; overflow: hidden; -webkit-mask-image: linear-gradient(#000 70%, transparent); mask-image: linear-gradient(#000 70%, transparent); }
.fold { align-self: center; margin-top: -6px; border: none; background: none; padding: 2px 10px; font-size: 13px; color: var(--c-primary); font-family: var(--font-sans); }
.fold:hover { background: var(--c-primary-soft); border-radius: var(--r-sm); }
.qc-answer {
  margin: 0 22px 14px; padding: 12px 16px; background: var(--c-paper); border-radius: var(--r-md);
  display: flex; flex-direction: column; gap: 6px; font-size: 14px; line-height: 1.8;
}
.qc-answer b { color: var(--c-primary); font-weight: 600; }
.qc-answer .ai { font-size: 12px; color: var(--c-accent); }
.none { color: var(--c-text-4); }
.qc-foot {
  border-top: 1px solid var(--c-divider); padding: 8px 12px 8px 22px; display: flex; flex-wrap: wrap; align-items: center;
  gap: 6px 10px; font-size: 13px; color: var(--c-text-4); background: var(--c-surface-2);
}
.sep { color: var(--c-border); }
/* 来源单行省略；inline-size 包含使其不撑宽页面（窄屏时收缩） */
.from { flex: 1 1 0; min-width: 120px; max-width: 420px; contain: inline-size; color: var(--c-text-4); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.from:hover { color: var(--c-primary); }
.acts { margin-left: auto; display: flex; align-items: center; gap: 4px; }
.act { border: none; background: none; padding: 4px 8px; font-size: 13px; color: var(--c-primary); border-radius: var(--r-sm); }
.act:hover, .act.on { background: var(--c-primary-soft); }
.add { border: none; border-radius: var(--r-sm); padding: 6px 14px; margin-left: 6px; font-size: 13px; background: var(--c-primary); color: #fff; }
.add.added { background: var(--c-paper); color: var(--c-text-3); }
</style>
