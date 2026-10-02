<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { bankApi, type ComposeMessage, type ComposeResult } from '@/api/bank'
import MathText from '@/components/MathText.vue'
import LoadingState from '@/components/LoadingState.vue'
import { CN_NUM, coefToDiff } from '@/data/mock'
import { useAppStore } from '@/stores/app'
import { fromBank, useBasketStore } from '@/stores/basket'

/** AI 组卷（Demo）：老师用文字描述学生情况与要求，AI 整理成组卷蓝图，程序从题库选题并赋分；可多轮修改 */
const { stage, subject } = storeToRefs(useAppStore())
const basket = useBasketStore()
const router = useRouter()

const EXAMPLES = [
  '高一学生，函数和三角函数比较薄弱，期中复习，中等难度',
  '学生基础较好，准备期末考试，出一份偏难的综合卷',
  '只考选择题和填空题，重点练集合与不等式',
]
const LETTERS = 'ABCDEFGH'
const DIFF_CLASS = { 容易: 'easy', 适中: 'mid', 较难: 'hard' } as const

const messages = ref<ComposeMessage[]>([])
const input = ref('')
const total = ref(100)
const difficulty = ref(0.45)
const busy = ref(false)
const error = ref('')
const result = ref<ComposeResult | null>(null)
const chatBox = ref<HTMLElement>()
let requestSeq = 0

const count = computed(() => result.value?.sections.reduce((a, s) => a + s.items.length, 0) ?? 0)
/** 各大题及题号（全卷连续编号） */
const sections = computed(() => {
  let no = 0
  return (result.value?.sections ?? []).map((s, i) => ({
    ...s, heading: `${CN_NUM[i] ?? i + 1}、${s.type}（共 ${s.items.length} 题，${s.score} 分）`,
    items: s.items.map((it) => ({ ...it, no: ++no })),
  }))
})

async function scrollToEnd() {
  await nextTick()
  chatBox.value?.scrollTo({ top: chatBox.value.scrollHeight, behavior: 'smooth' })
}

async function send(text = input.value) {
  const content = text.trim()
  if (!content || busy.value) return
  const seq = ++requestSeq
  const history = [...messages.value, { role: 'user' as const, content }]
  messages.value = history
  input.value = ''
  busy.value = true
  error.value = ''
  scrollToEnd()
  try {
    const r = await bankApi.compose({
      stage: stage.value, subject: subject.value, total: total.value, difficulty: difficulty.value, messages: history.slice(-20),
    })
    if (seq !== requestSeq) return
    result.value = r
    messages.value = [...history, { role: 'assistant', content: r.reply }]
    // 对话中调整了总分、难度时同步到设置
    if (r.total) total.value = r.total
    difficulty.value = r.difficulty
  } catch (e) {
    if (seq !== requestSeq) return
    // 失败时撤回这条消息，放回输入框便于重试
    messages.value = history.slice(0, -1)
    input.value = content
    error.value = (e as Error).message
  } finally {
    if (seq === requestSeq) {
      busy.value = false
      scrollToEnd()
    }
  }
}

function onKeydown(e: KeyboardEvent) {
  // 输入法选词时的回车不发送
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}

function restart() {
  requestSeq++
  busy.value = false
  messages.value = []
  result.value = null
  error.value = ''
}

// 切换学段学科后，此前的对话与试卷不再适用
watch([stage, subject], restart)

function toBasket() {
  const r = result.value
  if (!r) return
  if (basket.count && !confirm(`试题篮中已有 ${basket.count} 道题，将替换为本次组卷的 ${count.value} 道题，是否继续？`)) return
  const items = r.sections.flatMap((s) => s.items)
  basket.replace(items.map((it) => fromBank(it.question)))
  for (const it of items) basket.setScore(it.question.id, it.score)
  router.push('/paper')
}
</script>

<template>
  <main class="compose container">
    <section class="card chat sticky-side">
      <div class="chat-head">
        <span class="card-title">AI 组卷 · {{ stage }}{{ subject }}</span>
        <button v-if="messages.length" class="btn-link" :disabled="busy" @click="restart">重新开始</button>
      </div>

      <div ref="chatBox" class="chat-body">
        <div v-if="!messages.length" class="intro">
          <p>描述学生情况和组卷要求，AI 会从校本题库中选题并赋分。生成后可以继续提出修改，如「再简单一点」「多出两道解答题」。</p>
          <div class="examples">
            <button v-for="ex in EXAMPLES" :key="ex" class="example" @click="send(ex)">{{ ex }}</button>
          </div>
        </div>
        <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">{{ m.content }}</div>
        <div v-if="busy" class="msg assistant pending"><LoadingState compact label="AI 正在组卷…" /></div>
      </div>

      <div class="settings">
        <label>总分
          <input v-model.number="total" type="number" min="10" max="300" step="1" :disabled="busy">分
        </label>
        <label class="diff">难度
          <input v-model.number="difficulty" type="range" min="0.1" max="0.9" step="0.05" :disabled="busy">
          <span :class="DIFF_CLASS[coefToDiff(difficulty)]">{{ coefToDiff(difficulty) }} {{ difficulty.toFixed(2) }}</span>
        </label>
      </div>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div class="input">
        <textarea
          v-model="input" rows="3" maxlength="2000" :disabled="busy"
          :placeholder="messages.length ? '继续提出修改要求' : '如：高一学生，函数比较薄弱，期中复习'"
          @keydown="onKeydown"
        />
        <button class="btn btn-primary" :disabled="busy || !input.trim()" @click="send()"><LoadingState v-if="busy" compact label="组卷中…" /><template v-else>发送</template></button>
      </div>
      <p class="hint">请勿输入学生姓名等身份信息。Enter 发送，Shift + Enter 换行。</p>
    </section>

    <section class="card result" :aria-busy="busy">
      <LoadingState v-if="busy && !result" label="AI 正在为你组卷…" detail="正在匹配知识点、筛选题目并分配分值，请稍候" :rows="3" />
      <div v-else-if="!result" class="empty">
        <p>组好的试卷会显示在这里</p>
        <p class="muted">题目全部来自校本题库，总分与设置一致；确认后可加入试题篮，继续排版、导出 Word。</p>
      </div>
      <template v-else>
        <LoadingState v-if="busy" compact label="正在按新要求调整试卷…" />
        <div class="result-head">
          <div>
            <h1 class="serif">{{ result.title }}</h1>
            <div class="stats">
              <span>{{ count }} 题</span>
              <span>总分 {{ result.total }} 分</span>
              <span>
                平均难度 <b :class="DIFF_CLASS[coefToDiff(result.actualDifficulty)]">{{ result.actualDifficulty.toFixed(2) }}</b>
                （目标 {{ result.difficulty.toFixed(2) }}）
              </span>
              <span v-if="!result.ai" class="tag">关键词匹配</span>
            </div>
          </div>
          <button class="btn btn-primary" :disabled="busy || !count" @click="toBasket">加入试题篮并编辑</button>
        </div>

        <div v-if="result.focus.length" class="focus">
          <span class="label">重点知识点</span>
          <span v-for="f in result.focus" :key="f.name" class="chip is-soft" :title="f.path ?? f.name">{{ f.name }} × {{ f.count }}</span>
        </div>
        <ul v-if="result.gaps.length" class="gaps">
          <li v-for="g in result.gaps" :key="g">{{ g }}</li>
        </ul>

        <section v-for="s in sections" :key="s.type" class="sec">
          <h2>{{ s.heading }}</h2>
          <article v-for="it in s.items" :key="it.question.id" class="q">
            <div class="q-stem serif">
              <span class="no">{{ it.no }}．</span><span class="score">（{{ it.score }} 分）</span><MathText :text="it.question.stem" />
            </div>
            <div v-if="it.question.images.length" class="images">
              <img v-for="src in it.question.images" :key="src" :src="src" alt="题目配图" loading="lazy">
            </div>
            <div v-if="it.question.options.length" class="options serif">
              <span v-for="(o, i) in it.question.options" :key="i"><b>{{ LETTERS[i] }}．</b><MathText :text="o" /></span>
            </div>
            <div class="q-meta">
              <span :class="DIFF_CLASS[coefToDiff(it.question.coef)]">{{ coefToDiff(it.question.coef) }} {{ it.question.coef.toFixed(2) }}</span>
              <span v-for="k in it.question.knowledgePoints" :key="k.id" :title="k.path ?? k.name">{{ k.name }}</span>
              <span v-if="it.question.source" class="src" :title="it.question.source.label">{{ it.question.source.label }}</span>
            </div>
          </article>
        </section>
      </template>
    </section>
  </main>
</template>

<style scoped>
.compose { width: 100%; padding-top: 20px; padding-bottom: 48px; display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; }

/* 左侧对话 */
.chat { flex: 1 1 360px; max-width: 420px; padding: 16px; display: flex; flex-direction: column; gap: 12px; height: calc(100vh - var(--sticky-top) - 20px); min-height: 480px; }
.chat-head { display: flex; align-items: baseline; justify-content: space-between; }
.chat-body { flex: 1; overflow: auto; display: flex; flex-direction: column; gap: 10px; margin: 0 -6px; padding: 0 6px; }
.intro p { margin: 0 0 12px; font-size: 13px; line-height: 1.7; color: var(--c-text-3); }
.examples { display: flex; flex-direction: column; gap: 8px; }
.example {
  text-align: left; border: 1px dashed var(--c-primary-line); background: var(--c-surface-2); color: var(--c-text-2);
  border-radius: var(--r-md); padding: 8px 10px; font-size: 13px; line-height: 1.5;
}
.example:hover { border-color: var(--c-primary); color: var(--c-primary); }
.msg { max-width: 88%; padding: 8px 12px; border-radius: var(--r-md); font-size: 13px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
.msg.user { align-self: flex-end; background: var(--c-primary); color: #fff; }
.msg.assistant { align-self: flex-start; background: var(--c-paper); color: var(--c-text-2); }
.msg.pending { color: var(--c-text-4); }
.settings { display: flex; flex-wrap: wrap; gap: 8px 16px; font-size: 13px; color: var(--c-text-3); border-top: 1px solid var(--c-divider); padding-top: 12px; }
.settings label { display: flex; align-items: center; gap: 6px; }
.settings input[type=number] { width: 60px; height: 28px; border: 1px solid var(--c-border); border-radius: 4px; padding: 0 6px; text-align: center; font-family: inherit; }
.settings .diff { flex: 1; min-width: 200px; }
.settings .diff input { flex: 1; accent-color: var(--c-primary); }
.settings .diff span { width: 72px; font-size: 12px; }
.input { display: flex; gap: 8px; align-items: stretch; }
.input textarea {
  flex: 1; resize: none; border: 1px solid var(--c-border); border-radius: var(--r-md); padding: 8px 10px;
  font: inherit; font-size: 13px; line-height: 1.6; color: var(--c-ink);
}
.input textarea:focus { outline: none; border-color: var(--c-primary); }
.input .btn { height: auto; padding: 0 16px; }
.input .btn:disabled, .result-head .btn:disabled { opacity: .5; cursor: default; }
.hint { margin: -4px 0 0; font-size: 12px; color: var(--c-text-4); }
.error { margin: 0; font-size: 12px; color: var(--c-hard); }

/* 右侧试卷 */
.result { flex: 999 1 520px; min-width: 0; padding: 24px 28px; display: flex; flex-direction: column; gap: 16px; }
.empty { min-height: 360px; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 8px; color: var(--c-text-3); }
.empty p { margin: 0; font-size: 15px; }
.empty .muted { font-size: 13px; max-width: 360px; line-height: 1.7; }
.result-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.result-head h1 { margin: 0 0 8px; font-size: 22px; }
.result-head .btn { padding: 0 20px; }
.stats { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 16px; font-size: 13px; color: var(--c-text-3); }
.focus { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 13px; }
.focus .label { color: var(--c-text-3); margin-right: 4px; }
.gaps { margin: 0; padding: 10px 14px 10px 30px; background: #FDF6EC; border: 1px solid #F0D9B5; border-radius: var(--r-md); font-size: 13px; line-height: 1.7; color: #8A5A14; }
.sec h2 { margin: 8px 0 10px; font-size: 16px; }
.q { padding: 12px 0; border-top: 1px solid var(--c-divider); display: flex; flex-direction: column; gap: 8px; }
.q-stem { font-size: 15px; line-height: 1.8; }
.no { font-weight: 600; }
.score { color: var(--c-text-3); font-size: 13px; margin-right: 4px; }
.options { display: flex; flex-wrap: wrap; gap: 4px 28px; font-size: 15px; line-height: 1.8; padding-left: 1.6em; }
.options b { font-weight: 400; }
.images img { max-width: 280px; max-height: 200px; }
.q-meta { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px; color: var(--c-text-4); }
.q-meta .src { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.easy { color: var(--c-easy); }
.mid { color: var(--c-mid); }
.hard { color: var(--c-hard); }

@media (max-width: 900px) {
  .chat { max-width: none; height: auto; min-height: 0; position: static; }
  .chat-body { max-height: 360px; }
}
</style>
