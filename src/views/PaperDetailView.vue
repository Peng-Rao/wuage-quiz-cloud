<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { TYPE_ORDER, coefToDiff } from '@/data/mock'
import { bankApi, type PaperDetail } from '@/api/bank'
import { useAppStore } from '@/stores/app'
import { fromBank, useBasketStore } from '@/stores/basket'
import BankQuestionCard from '@/components/bank/BankQuestionCard.vue'
import DifficultyBar from '@/components/DifficultyBar.vue'
import ModalDialog from '@/components/ModalDialog.vue'

const props = defineProps<{ id: string }>()
const router = useRouter()
const basket = useBasketStore()
const app = useAppStore()

const paper = ref<PaperDetail | null>(null)
const error = ref('')
watch(() => props.id, async (id) => {
  paper.value = null
  error.value = ''
  try {
    const p = await bankApi.getPaper(id)
    if (props.id === id) paper.value = p
  } catch (e) {
    if (props.id === id) error.value = (e as Error).message
  }
}, { immediate: true })

const qs = computed(() => paper.value?.questions ?? [])
const diffCount = (d: string) => qs.value.filter((q) => coefToDiff(q.coef) === d).length
const types = computed(() => TYPE_ORDER.filter((t) => paper.value?.typeCounts[t])
  .map((t) => ({ t, n: paper.value!.typeCounts[t]!, score: qs.value.filter((q) => q.type === t).reduce((a, q) => a + q.score, 0) })))
const inBasket = computed(() => qs.value.filter((q) => basket.has(q.id)).length)
const tags = computed(() => {
  const m = paper.value?.meta
  return m ? [m.stage, m.grade, m.subject, m.paperType, m.region, m.schoolYear, m.textbook].filter(Boolean) : []
})

const allAns = ref(false)
const ansOpen = reactive<Record<string, boolean>>({})
const showAns = (id: string) => ansOpen[id] ?? allAns.value
function toggleAllAns() {
  allAns.value = !allAns.value
  for (const k of Object.keys(ansOpen)) delete ansOpen[k]
}

function addAll() {
  basket.addMany(qs.value.map(fromBank))
}

/** 用此卷组卷：试题篮换成本卷的题，保留原卷题序与分值 */
const confirmOpen = ref(false)
function useAsPaper() {
  if (basket.count && basket.items.some((x) => !qs.value.some((q) => q.id === x.q.id))) confirmOpen.value = true
  else replaceAndGo()
}
/** 移出试卷库（如重复入库的试卷） */
const removeOpen = ref(false)
const removing = ref(false)
async function removePaper() {
  removing.value = true
  try {
    await bankApi.removePaper(props.id)
    for (const q of qs.value) basket.remove(q.id)
    router.replace('/papers')
  } catch (e) {
    error.value = (e as Error).message
    removeOpen.value = false
  } finally {
    removing.value = false
  }
}

function replaceAndGo() {
  basket.replace(qs.value.map(fromBank))
  // 卷头学科等随当前学段学科显示，切换到本卷的学段学科
  const m = paper.value?.meta
  if (m?.stage && m.subject) app.pickSubject(m.stage, m.subject)
  confirmOpen.value = false
  router.push('/paper')
}
</script>

<template>
  <main class="detail container">
    <RouterLink to="/papers" class="back">‹ 试卷库</RouterLink>

    <div v-if="error" class="card empty err">{{ error }}</div>
    <div v-else-if="!paper" class="card empty">加载中…</div>

    <template v-else>
      <section class="card head">
        <div class="head-main">
          <h1 class="serif">{{ paper.title }}</h1>
          <div class="tags"><span v-for="t in tags" :key="t" class="chip is-soft">{{ t }}</span></div>
          <div class="stats">
            <div><b>{{ paper.questionCount }}</b><span>题</span></div>
            <div><b>{{ paper.totalScore }}</b><span>总分</span></div>
            <div v-if="paper.avgCoef !== null"><b>{{ paper.avgCoef.toFixed(2) }}</b><span>难度 · {{ coefToDiff(paper.avgCoef) }}</span></div>
            <div class="types">
              <span v-for="x in types" :key="x.t">{{ x.t }} {{ x.n }} 题 · {{ x.score }} 分</span>
            </div>
          </div>
          <div class="dist-wrap">
            <DifficultyBar :easy="diffCount('容易')" :mid="diffCount('适中')" :hard="diffCount('较难')" />
          </div>
          <p v-if="paper.sourceQuestionCount > paper.questionCount" class="partial">
            原卷共 {{ paper.sourceQuestionCount }} 题，已入库 {{ paper.questionCount }} 题；其余题目可在「试卷解析」中核对后保存。
          </p>
        </div>
        <div class="head-ops">
          <button class="btn btn-primary" @click="useAsPaper">用此卷组卷</button>
          <button class="btn btn-outline" :disabled="inBasket === qs.length" @click="addAll">
            {{ inBasket === qs.length ? '已全部加入试题篮' : '整卷加入试题篮' }}
          </button>
          <span class="basket-note">本卷 {{ inBasket }} / {{ qs.length }} 题在试题篮中</span>
          <button class="btn-link remove" @click="removeOpen = true">移出试卷库</button>
        </div>
      </section>

      <div class="toolbar">
        <span>按原卷题序</span>
        <button class="all-ans" @click="toggleAllAns">{{ allAns ? '收起全部解析' : '展开全部解析' }}</button>
      </div>

      <div class="list">
        <BankQuestionCard
          v-for="(q, i) in qs" :key="q.id" :q="q" :no="q.source?.no ?? i + 1" hide-source
          :show-answer="showAns(q.id)" :in-basket="basket.has(q.id)"
          @toggle-answer="ansOpen[q.id] = !showAns(q.id)" @toggle-basket="basket.toggle(fromBank(q))"
        />
      </div>
    </template>

    <ModalDialog v-model="removeOpen" title="移出试卷库" :width="420">
      <p class="confirm">
        将从校本题库删除本卷已入库的 {{ qs.length }} 道题，试卷库中不再显示这份试卷。原卷的解析结果保留，可在「试卷解析」中重新保存。
      </p>
      <template #footer>
        <button class="btn" @click="removeOpen = false">取消</button>
        <button class="btn btn-danger" :disabled="removing" @click="removePaper">{{ removing ? '移出中…' : '移出试卷库' }}</button>
      </template>
    </ModalDialog>

    <ModalDialog v-model="confirmOpen" title="用此卷组卷" :width="420">
      <p class="confirm">试题篮中已有 {{ basket.count }} 道题，将被替换为本卷的 {{ qs.length }} 道题（保留原卷题序与分值）。</p>
      <template #footer>
        <button class="btn" @click="confirmOpen = false">取消</button>
        <button class="btn btn-primary" @click="replaceAndGo">替换并组卷</button>
      </template>
    </ModalDialog>
  </main>
</template>

<style scoped>
.detail { padding-top: 18px; padding-bottom: 56px; display: flex; flex-direction: column; gap: 14px; max-width: 1000px; }
.back { font-size: 13px; align-self: flex-start; }
.empty { padding: 40px; text-align: center; color: var(--c-text-4); font-size: 14px; }
.empty.err { color: var(--c-hard); }

.head { padding: 22px 24px; display: flex; flex-wrap: wrap; gap: 20px; }
.head-main { flex: 1 1 420px; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.head h1 { margin: 0; font-size: 21px; line-height: 1.5; font-weight: 700; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tags .chip { cursor: default; font-size: 12px; padding: 3px 8px; }
.stats { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px 28px; }
.stats > div { display: flex; align-items: baseline; gap: 4px; }
.stats b { font-size: 22px; font-weight: 700; color: var(--c-ink); }
.stats span { font-size: 12px; color: var(--c-text-3); }
.stats .types { flex-wrap: wrap; gap: 4px 12px; }
.dist-wrap { max-width: 360px; display: flex; flex-direction: column; gap: 6px; }
.partial { margin: 0; font-size: 12px; color: #B5661B; }
.head-ops { flex: 0 0 200px; display: flex; flex-direction: column; gap: 10px; justify-content: center; }
.head-ops .btn:disabled { opacity: .55; cursor: default; }
.remove { align-self: center; font-size: 12px; color: var(--c-text-4); }
.remove:hover { color: var(--c-hard); }
.btn-danger { background: var(--c-hard); border-color: var(--c-hard); color: #fff; }
.basket-note { font-size: 12px; color: var(--c-text-4); text-align: center; }

.toolbar { display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: var(--c-text-3); padding: 0 4px; }
.all-ans { border: 1px solid var(--c-border); background: #fff; border-radius: var(--r-sm); padding: 4px 10px; font-size: 13px; color: var(--c-text-2); }
.list { display: flex; flex-direction: column; gap: 14px; }
.confirm { margin: 0; font-size: 14px; line-height: 1.8; color: var(--c-text-2); }

@media (max-width: 640px) {
  .head { padding: 18px; }
  .head-ops { flex: 1 1 100%; }
}
</style>
