<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { MY_PAPERS, PAPERS } from '@/data/mock'

const router = useRouter()
const keyword = ref('')
const HOT = ['函数零点', '数列求和', '三角恒等变换', '2026 新高考 I 卷', '立体几何']

const ENTRIES = [
  { mark: '章', title: '章节选题', desc: '按教材版本与章节同步选题', to: '/pick' },
  { mark: '知', title: '知识点选题', desc: '按知识体系精准定位考点', to: { path: '/pick', query: { tree: '知识点' } } },
  { mark: '智', title: '智能组卷', desc: '设定题量与难度，自动生成试卷', to: '/paper' },
  { mark: '卷', title: '试卷库', desc: '按年级、学科浏览已入库的整套试卷', to: '/papers' },
]

const paperTab = ref('期中')

function search(q = keyword.value) {
  router.push({ path: '/pick', query: q.trim() ? { q: q.trim() } : {} })
}
</script>

<template>
  <main>
    <section class="hero">
      <div class="container hero-inner">
        <h1 class="serif">为老师准备的干净题库</h1>
        <p>按教材章节或知识点选题，加入试题篮后一键排版，导出 Word / PDF 试卷。</p>
        <form class="search" @submit.prevent="search()">
          <input v-model="keyword" placeholder="输入题干关键词、知识点或试卷名称" aria-label="搜索">
          <button type="submit">搜 题</button>
        </form>
        <div class="hot">
          <span>热门：</span>
          <button v-for="h in HOT" :key="h" type="button" @click="search(h)">{{ h }}</button>
        </div>
      </div>
    </section>

    <div class="container body">
      <div class="entries">
        <RouterLink v-for="e in ENTRIES" :key="e.title" :to="e.to" class="card entry">
          <div class="entry-head">
            <span class="entry-mark serif">{{ e.mark }}</span>
            <span class="entry-title">{{ e.title }}</span>
          </div>
          <span class="entry-desc">{{ e.desc }}</span>
        </RouterLink>
      </div>

      <div class="split">
        <section class="card papers">
          <div class="papers-head">
            <h2>最新试卷</h2>
            <div class="tabs">
              <button
                v-for="k in Object.keys(PAPERS)" :key="k" class="chip"
                :class="{ 'is-soft': k === paperTab }" @click="paperTab = k"
              >{{ k }}</button>
            </div>
            <RouterLink to="/papers" class="more">更多 ›</RouterLink>
          </div>
          <div>
            <div v-for="p in PAPERS[paperTab]" :key="p.title" class="paper-row">
              <span class="tag">{{ p.tag }}</span>
              <span class="paper-title">{{ p.title }}</span>
              <span class="paper-region">{{ p.region }}</span>
              <span class="paper-date">{{ p.date }}</span>
            </div>
          </div>
        </section>

        <aside class="side">
          <section class="card side-card">
            <h2>我的组卷</h2>
            <div class="mine">
              <div v-for="m in MY_PAPERS" :key="m.title" class="mine-item">
                <span class="mine-title">{{ m.title }}</span>
                <span class="mine-meta">{{ m.meta }}</span>
              </div>
            </div>
          </section>
          <section class="school">
            <span class="school-title">校本题库</span>
            <span class="school-desc">上传本校试卷，自动拆分为单题，与教研组共享。</span>
            <RouterLink to="/upload" class="school-btn">上传试卷</RouterLink>
          </section>
        </aside>
      </div>
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
