import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue'), meta: { title: '首页' } },
    { path: '/chapter', name: 'chapter', component: () => import('./views/ChapterPickView.vue'), meta: { title: '章节选题' } },
    { path: '/knowledge', name: 'knowledge-pick', component: () => import('./views/KnowledgePickView.vue'), meta: { title: '知识点选题' } },
    // 旧地址：?tree=知识点 进入知识点选题，其余进入章节选题
    { path: '/pick', redirect: (to) => ({ path: to.query.tree === '知识点' ? '/knowledge' : '/chapter', query: { q: to.query.q } }) },
    { path: '/papers', name: 'papers', component: () => import('./views/PapersView.vue'), meta: { title: '试卷选题' } },
    { path: '/papers/:id', name: 'paper-detail', component: () => import('./views/PaperDetailView.vue'), props: true, meta: { title: '试卷详情' } },
    { path: '/paper', name: 'paper', component: () => import('./views/PaperView.vue'), meta: { title: '试卷编辑' } },
    { path: '/upload', name: 'upload', component: () => import('./views/UploadView.vue'), meta: { title: '试卷解析' } },
    { path: '/upload/knowledge', name: 'knowledge', component: () => import('./views/KnowledgeView.vue'), meta: { title: '知识树管理' } },
    { path: '/upload/eval', name: 'eval', component: () => import('./views/EvalView.vue'), meta: { title: '解析评测' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · 福格云上题库` : '福格云上题库'
})
