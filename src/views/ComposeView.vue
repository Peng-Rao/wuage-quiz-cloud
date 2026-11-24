<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { bankApi, type ComposeMessage, type ComposeModels, type ComposeResult, type ComposeSessionItem } from '@/api/bank'
import MathText from '@/components/MathText.vue'
import MaterialText from '@/components/MaterialText.vue'
import LoadingState from '@/components/LoadingState.vue'
import { CN_NUM, coefToDiff } from '@/data/mock'
import { useAppStore } from '@/stores/app'
import { noSep, optionCols, optionLabel, provideSubject } from '@/utils/subject'
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
const layout = provideSubject(subject)
const DIFF_CLASS = { 容易: 'easy', 适中: 'mid', 较难: 'hard' } as const

const messages = ref<ComposeMessage[]>([])
const input = ref('')
const models = ref<ComposeModels | null>(null)
const model = ref('')
const total = ref(100)
const difficulty = ref(0.45)
const busy = ref(false)
const error = ref('')
const result = ref<ComposeResult | null>(null)
const chatBox = ref<HTMLElement>()
let requestSeq = 0

// 组卷记录：每次组卷自动保存为本人的记录，可继续对话、重新打开或删除
const sessionId = ref<string | null>(null)
const sessions = ref<ComposeSessionItem[]>([])
const showHistory = ref(false)
const opening = ref('')
const current = computed(() => sessions.value.find((s) => s.id === sessionId.value))

async function loadSessions() {
  const [st, sub] = [stage.value, subject.value]
  try {
    const list = await bankApi.listComposeSessions(st, sub)
    if (st === stage.value && sub === subject.value) sessions.value = list
  } catch { /* 记录列表加载失败不影响组卷 */ }
}

onMounted(async () => {
  void loadSessions()
  models.value = await bankApi.listComposeModels().catch(() => null)
})

const when = (s: string) => new Date(s).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })

async function openSession(id: string) {
  if (busy.value || opening.value) return
  opening.value = id
  error.value = ''
  try {
    const d = await bankApi.getComposeSession(id)
    requestSeq++
    sessionId.value = d.id
    messages.value = d.messages
    result.value = d.result
    total.value = d.total
    difficulty.value = d.difficulty
    // 记录所用模型仍可选时沿用，否则用默认模型
    model.value = d.model && d.model !== models.value?.defaultModel && models.value?.models.includes(d.model) ? d.model : ''
    showHistory.value = false
    scrollToEnd()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    opening.value = ''
  }
}

async function renameSession(s: ComposeSessionItem) {
  const title = window.prompt('重命名组卷记录', s.title)?.trim()
  if (!title || title === s.title) return
  try {
    const updated = await bankApi.renameComposeSession(s.id, title)
    sessions.value = sessions.value.map((x) => (x.id === s.id ? updated : x))
  } catch (e) { error.value = (e as Error).message }
}

async function removeSession(s: ComposeSessionItem) {
  if (!confirm(`删除组卷记录「${s.title}」？删除后无法恢复。`)) return
  try {
    await bankApi.deleteComposeSession(s.id)
    sessions.value = sessions.value.filter((x) => x.id !== s.id)
    if (sessionId.value === s.id) restart()
  } catch (e) { error.value = (e as Error).message }
}

function newSession() {
  restart()
  showHistory.value = false
}

const alternateModels = computed(() => models.value?.models.filter((id) => id !== models.value?.defaultModel) ?? [])
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
      model: model.value || undefined, sessionId: sessionId.value ?? undefined,
    })
    if (seq !== requestSeq) return
    result.value = r
    sessionId.value = r.sessionId ?? sessionId.value
    void loadSessions()
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
  sessionId.value = null
}

// 切换学段学科后，此前的对话与试卷不再适用；记录列表换成该学科的
watch([stage, subject], () => {
  restart()
  showHistory.value = false
  sessions.value = []
  void loadSessions()
})

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
        <span class="head-actions">
          <button class="btn-link" :class="{ 'is-primary': showHistory }" :disabled="busy" :aria-expanded="showHistory" @click="showHistory = !showHistory">
            历史记录{{ sessions.length ? `（${sessions.length}）` : '' }}
          </button>
          <button v-if="messages.length || showHistory" class="btn-link" :disabled="busy" @click="newSession">新建组卷</button>
        </span>
      </div>
      <p v-if="current && !showHistory" class="session-title" :title="current.title">当前记录：{{ current.title }}</p>

      <div v-if="showHistory" class="history">
        <p v-if="!sessions.length" class="history-empty">还没有{{ stage }}{{ subject }}的组卷记录。组卷后会自动保存在这里，只有你自己能看到。</p>
        <div v-for="s in sessions" :key="s.id" class="session" :class="{ current: s.id === sessionId }">
          <button class="session-main" :disabled="!!opening" @click="openSession(s.id)">
            <b>{{ s.title }}</b>
            <span v-if="s.preview">{{ s.preview }}</span>
            <small>{{ s.turns }} 轮对话 · {{ s.questionCount }} 题 · {{ s.total }} 分 · {{ when(s.updatedAt) }}</small>
            <LoadingState v-if="opening === s.id" compact label="正在打开…" />
          </button>
          <span class="session-ops">
            <button class="btn-link" @click="renameSession(s)">重命名</button>
            <button class="btn-link danger" @click="removeSession(s)">删除</button>
          </span>
        </div>
      </div>

      <div v-else ref="chatBox" class="chat-body">
        <div v-if="!messages.length" class="intro">
          <p>描述学生情况和组卷要求，AI 会从校本题库中选题并赋分。生成后可以继续提出修改，如「再简单一点」「多出两道解答题」。</p>
          <div class="examples">
            <button v-for="ex in EXAMPLES" :key="ex" class="example" @click="send(ex)">{{ ex }}</button>
          </div>
          <div v-if="sessions.length" class="recent">
            <span class="recent-label">继续最近的组卷</span>
            <button v-for="s in sessions.slice(0, 3)" :key="s.id" class="recent-item" :disabled="!!opening" @click="openSession(s.id)">
              <span>{{ s.title }}</span><small>{{ when(s.updatedAt) }}</small>
            </button>
            <button v-if="sessions.length > 3" class="btn-link is-primary" @click="showHistory = true">查看全部 {{ sessions.length }} 条 →</button>
          </div>
        </div>
        <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">{{ m.content }}</div>
        <div v-if="busy" class="msg assistant pending"><LoadingState compact label="AI 正在组卷…" /></div>
      </div>

      <div class="settings">
        <label v-if="models?.defaultModel" class="model">模型
          <select v-model="model" :disabled="busy">
            <option value="">默认（{{ models.defaultModel }}）</option>
            <option v-for="id in alternateModels" :key="id" :value="id">{{ id }}</option>
          </select>
        </label>
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
      <p class="hint">请勿输入学生姓名等身份信息。Enter 发送，Shift + Enter 换行。组卷记录自动保存，仅本人可见。</p>
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
              <span v-else-if="result.model" class="tag">{{ result.model }}</span>
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
            <MaterialText v-if="it.question.material" :text="it.question.material" initial="collapsed" />
            <div class="q-stem serif" :class="`lay-${layout}`">
              <span class="no">{{ it.no }}{{ noSep(layout) }}</span><span class="score">（{{ it.score }} 分）</span><MathText :text="it.question.stem" />
            </div>
            <div v-if="it.question.images.length" class="images">
              <img v-for="src in it.question.images" :key="src" :src="src" alt="题目配图" loading="lazy">
            </div>
            <div
              v-if="it.question.options.length" class="options serif"
              :class="[`lay-${layout}`, layout !== 'plain' && ['opt-fixed', `cols-${optionCols(it.question.options)}`]]"
            >
              <span v-for="(o, i) in it.question.options" :key="i"><b>{{ optionLabel(layout, i) }}</b><MathText :text="o" /></span>
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
.head-actions { display: flex; gap: 14px; }
.session-title { margin: -6px 0 0; font-size: 12px; color: var(--c-text-4); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent { margin-top: 18px; display: flex; flex-direction: column; gap: 6px; }
.recent-label { font-size: 12px; color: var(--c-text-4); }
.recent-item { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; text-align: left; border: 1px solid var(--c-border); background: var(--c-surface); border-radius: var(--r-md); padding: 8px 10px; font-size: 13px; color: var(--c-text-2); }
.recent-item span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent-item small { flex-shrink: 0; color: var(--c-text-4); font-size: 12px; }
.recent-item:hover { border-color: var(--c-primary-line); color: var(--c-primary); }
.recent .btn-link { align-self: flex-start; }
.history { flex: 1; overflow: auto; display: flex; flex-direction: column; gap: 8px; margin: 0 -6px; padding: 0 6px; }
.history-empty { margin: 8px 0; font-size: 13px; line-height: 1.7; color: var(--c-text-3); }
.session { border: 1px solid var(--c-border); border-radius: var(--r-md); background: var(--c-surface); }
.session.current { border-color: var(--c-primary-line); background: var(--c-primary-tint); }
.session-main { width: 100%; display: flex; flex-direction: column; gap: 3px; text-align: left; border: none; background: transparent; padding: 10px 12px 4px; color: var(--c-ink); }
.session-main b { font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-main span { font-size: 12px; color: var(--c-text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-main small { font-size: 12px; color: var(--c-text-4); }
.session-main:hover b { color: var(--c-primary); }
.session-ops { display: flex; justify-content: flex-end; gap: 14px; padding: 0 12px 8px; }
.session-ops .danger { color: var(--c-danger); }
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
.settings .model { flex: 1 1 100%; }
.settings select { max-width: 100%; min-height: 28px; border: 1px solid var(--c-border); border-radius: 4px; padding: 0 6px; background: var(--c-surface); color: var(--c-text-2); font: inherit; }
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
.gaps { margin: 0; padding: 10px 14px 10px 30px; background: var(--c-warn-soft); border: 1px solid var(--c-warn-line); border-radius: var(--r-md); font-size: 13px; line-height: 1.7; color: var(--c-warn); }
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
