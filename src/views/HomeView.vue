<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { bankApi, type PaperSummary } from '@/api/bank'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import { useBasketStore } from '@/stores/basket'
import LoadingState from '@/components/LoadingState.vue'
const auth = useAuthStore()
const app = useAppStore()
const basket = useBasketStore()
const router = useRouter()
const keyword = ref('')
const papers = ref<PaperSummary[]>([])
const error = ref('')
const loading = ref(false)
let sequence = 0
watch(() => [app.stage, app.subject], async () => {
  const seq = ++sequence; loading.value = true; error.value = ''
  try {
    const result = await bankApi.listPapers({ stage: app.stage, subject: app.subject, limit: 5 })
    if (seq === sequence) papers.value = result.items
  } catch (e) { if (seq === sequence) error.value = (e as Error).message }
  finally { if (seq === sequence) loading.value = false }
}, { immediate: true })
const entries = computed(() => [
  { mark: '章', title: '章节选题', desc: '按教材版本与章节同步选题', to: '/chapter' },
  { mark: '知', title: '知识点选题', desc: '按可见题目的知识体系定位考点', to: '/knowledge' },
  auth.isStaff
    ? { mark: '卷', title: 'AI 组卷', desc: '描述学生情况与要求，AI 选题并赋分', to: '/compose' }
    : { mark: '卷', title: '手动组卷', desc: '自由选题排版，下载教学试卷', to: '/paper' },
  { mark: '题', title: '试卷选题', desc: '浏览权限范围内的已入库试卷', to: '/papers' },
])
function search() { router.push({ path: '/knowledge', query: keyword.value.trim() ? { q: keyword.value.trim() } : {} }) }
</script>
<template>
  <main>
    <section class="hero"><div class="container hero-inner">
      <h1 class="serif">{{ auth.user?.displayName }}，欢迎回来</h1>
      <p>{{ auth.isAdmin ? '管理全校题库、账号与教研协作。' : auth.isStaff ? '专注所负责学科，上传、审核与 AI 组卷。' : '你的题库仅展示分配给本人且审核通过的题目与知识点，可手动组卷并下载。' }}</p>
      <form class="search" @submit.prevent="search"><input v-model="keyword" placeholder="输入题干关键词、知识点或试卷名称" aria-label="搜索"><button type="submit">搜 题</button></form>
    </div></section>
    <div class="container body">
      <div class="entries"><RouterLink v-for="e in entries" :key="e.to" :to="e.to" class="card entry"><div class="entry-head"><span class="entry-mark serif">{{ e.mark }}</span><span class="entry-title">{{ e.title }}</span></div><span class="entry-desc">{{ e.desc }}</span></RouterLink></div>
      <div class="split"><section class="card papers"><div class="papers-head"><h2>{{ auth.isStaff ? '最新试卷' : '我的已审核题目' }}</h2><RouterLink to="/papers" class="more">更多 ›</RouterLink></div>
        <p v-if="error" role="alert">{{ error }}</p><LoadingState v-else-if="loading" :compact="!!papers.length" label="正在加载最新试卷…" :rows="papers.length ? 0 : 2" /><p v-else-if="!papers.length">当前学科暂无可查看的试卷{{ auth.isStaff ? '，可上传试卷开始整理。' : '，请联系管理员或组长审核分配。' }}</p>
        <RouterLink v-for="p in papers" :key="p.id" :to="`/papers/${p.id}`" class="paper-row"><span class="tag">{{ p.meta.subject }}</span><span class="paper-title">{{ p.title }}</span><span class="paper-date">{{ p.questionCount }} 道题</span></RouterLink>
      </section><aside class="side"><section class="card side-card"><h2>我的组卷</h2><div class="mine"><span class="mine-title">试题篮已选 {{ basket.count }} 道题</span><span class="mine-meta">当前合计 {{ basket.totalScore }} 分</span><RouterLink to="/paper">继续组卷与下载 ›</RouterLink></div></section>
        <section v-if="auth.isStaff" class="school"><span class="school-title">校本题库</span><span class="school-desc">上传试卷，AI 解析后核对入库，再审核分配给老师。</span><RouterLink to="/upload" class="school-btn">上传试卷</RouterLink></section>
      </aside></div>
    </div>
  </main>
</template>
<style scoped>
.hero { background: var(--c-primary); color: #fff; }
.hero-inner { padding-top: 56px; padding-bottom: 64px; display: flex; flex-direction: column; gap: 22px; }
.hero h1 { margin: 0; font-size: 36px; font-weight: 700; letter-spacing: 1px; }
.hero p { margin: 0; font-size: 16px; color: #FFF1E6; max-width: 620px; line-height: 1.7; }
.search { display: flex; max-width: 720px; background: #fff; border-radius: 10px; padding: 6px; gap: 6px; }
.search input { flex: 1; min-width: 0; border: none; outline: none; font-size: 15px; padding: 0 14px; color: var(--c-ink); background: transparent; }
.search button { border: none; background: var(--c-ink); color: #fff; border-radius: 7px; padding: 0 26px; height: 42px; font-size: 15px; font-weight: 600; }
.hot { display: flex; gap: 10px; flex-wrap: wrap; font-size: 13px; color: #FFF1E6; }
.hot button { border: none; background: none; padding: 0; color: inherit; font-size: inherit; }
.hot button:hover { color: #fff; text-decoration: underline; }

.body { margin-top: -28px; padding-bottom: 56px; display: flex; flex-direction: column; gap: 28px; }
.entries { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
.entry {
  padding: 20px; display: flex; flex-direction: column; gap: 8px; color: var(--c-ink);
  box-shadow: 0 4px 14px rgba(27, 36, 48, .05); transition: border-color .15s;
}
.entry:hover { border-color: var(--c-primary); text-decoration: none; color: var(--c-ink); }
.entry-head { display: flex; align-items: center; gap: 10px; }
.entry-mark {
  width: 34px; height: 34px; border-radius: var(--r-md); background: var(--c-primary-soft); color: var(--c-primary);
  display: flex; align-items: center; justify-content: center; font-weight: 700;
}
.entry-title { font-size: 16px; font-weight: 600; }
.entry-desc { font-size: 13px; color: var(--c-text-3); line-height: 1.6; }

.split { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 24px; }
.papers { padding: 20px 24px; }
.papers-head { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 18px; border-bottom: 1px solid var(--c-divider); padding-bottom: 12px; }
.papers-head h2 { margin: 0; font-size: 17px; font-weight: 600; }
.tabs { display: flex; gap: 4px; }
.more { margin-left: auto; font-size: 13px; }
.paper-row { display: flex; align-items: center; gap: 14px; padding: 13px 0; border-bottom: 1px dashed var(--c-divider); }
.paper-row .tag { padding: 2px 6px; }
.paper-title { flex: 1; font-size: 14px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.paper-region, .paper-date { font-size: 12px; color: var(--c-text-4); flex-shrink: 0; }
.paper-date { width: 72px; text-align: right; }

.side { display: flex; flex-direction: column; gap: 16px; }
.side-card { padding: 20px; }
.side-card h2 { margin: 0 0 12px; font-size: 16px; font-weight: 600; }
.mine { display: flex; flex-direction: column; gap: 12px; }
.mine-item { display: flex; flex-direction: column; gap: 3px; }
.mine-title { font-size: 14px; }
.mine-meta { font-size: 12px; color: var(--c-text-4); }
.school { background: #FBF3EA; border: 1px solid #F0DCC4; border-radius: var(--r-lg); padding: 20px; display: flex; flex-direction: column; gap: 8px; }
.school-title { font-size: 15px; font-weight: 600; color: #8A4B12; }
.school-desc { font-size: 13px; color: #6A5A48; line-height: 1.6; }
.school-btn {
  align-self: flex-start; margin-top: 4px; border: 1px solid var(--c-ink); background: #fff; color: #B5661B;
  border-radius: var(--r-sm); padding: 5px 12px; font-size: 13px;
}
.school-btn:hover { text-decoration: none; background: var(--c-primary-soft); }

@media (max-width: 900px) {
  .split { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 600px) {
  .hero h1 { font-size: 28px; }
  .search button { padding: 0 16px; }
  .paper-region { display: none; }
  .paper-date { width: auto; }
}
</style>
