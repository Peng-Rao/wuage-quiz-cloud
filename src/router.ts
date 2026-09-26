import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue'), meta: { title: '首页' } },
    { path: '/pick', name: 'pick', component: () => import('./views/PickView.vue'), meta: { title: '选题组卷' } },
    { path: '/papers', name: 'papers', component: () => import('./views/PapersView.vue'), meta: { title: '试卷库' } },
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
