import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue'), meta: { title: '首页' } },
    { path: '/pick', name: 'pick', component: () => import('./views/PickView.vue'), meta: { title: '选题组卷' } },
    { path: '/paper', name: 'paper', component: () => import('./views/PaperView.vue'), meta: { title: '试卷编辑' } },
    { path: '/upload', name: 'upload', component: () => import('./views/UploadView.vue'), meta: { title: '试卷解析' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · 福格云上题库` : '福格云上题库'
})
