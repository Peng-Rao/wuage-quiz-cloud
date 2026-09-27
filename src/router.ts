import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { title: '登录', public: true } },
    { path: '/users', component: () => import('./views/UsersView.vue'), meta: { title: '账号管理', admin: true } },
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue'), meta: { title: '首页' } },
    { path: '/chapter', name: 'chapter', component: () => import('./views/ChapterPickView.vue'), meta: { title: '章节选题' } },
    { path: '/knowledge', name: 'knowledge-pick', component: () => import('./views/KnowledgePickView.vue'), meta: { title: '知识点选题' } },
    // 旧地址：?tree=知识点 进入知识点选题，其余进入章节选题
    { path: '/pick', redirect: (to) => ({ path: to.query.tree === '知识点' ? '/knowledge' : '/chapter', query: { q: to.query.q } }) },
    { path: '/papers', name: 'papers', component: () => import('./views/PapersView.vue'), meta: { title: '试卷选题' } },
    { path: '/papers/:id', name: 'paper-detail', component: () => import('./views/PaperDetailView.vue'), props: true, meta: { title: '试卷详情' } },
    { path: '/paper', name: 'paper', component: () => import('./views/PaperView.vue'), meta: { title: '试卷编辑' } },
    { path: '/upload', name: 'upload', component: () => import('./views/UploadView.vue'), meta: { title: '试卷解析', staff: true } },
    { path: '/upload/knowledge', name: 'knowledge', component: () => import('./views/KnowledgeView.vue'), meta: { title: '知识树管理', staff: true } },
    { path: '/upload/eval', name: 'eval', component: () => import('./views/EvalView.vue'), meta: { title: '解析评测', admin: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async to => {
  const auth = useAuthStore()
  await auth.restore()
  if (!auth.user && !to.meta.public) return { path: "/login", query: { redirect: to.fullPath } }
  if (auth.user && to.path === "/login") return "/"
  if ((to.meta.admin && !auth.isAdmin) || (to.meta.staff && !auth.isStaff)) return "/"
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · 福格云上题库` : '福格云上题库'
})
