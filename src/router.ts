import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useConsoleStore } from '@/stores/console'
import type { Permission } from '@/api/admin'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { title: '登录', public: true } },
    // 账号管理与 AI 成本已移入管理后台（/admin），旧地址跳转过去
    { path: '/users', redirect: '/admin/users' },
    { path: '/usage', redirect: '/admin/usage' },
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue'), meta: { title: '首页' } },
    { path: '/chapter', name: 'chapter', component: () => import('./views/ChapterPickView.vue'), meta: { title: '章节选题' } },
    { path: '/knowledge', name: 'knowledge-pick', component: () => import('./views/KnowledgePickView.vue'), meta: { title: '知识点选题' } },
    // 旧地址：?tree=知识点 进入知识点选题，其余进入章节选题
    { path: '/pick', redirect: (to) => ({ path: to.query.tree === '知识点' ? '/knowledge' : '/chapter', query: { q: to.query.q } }) },
    { path: '/compose', name: 'compose', component: () => import('./views/ComposeView.vue'), meta: { title: 'AI 组卷', staff: true } },
    { path: '/papers', name: 'papers', component: () => import('./views/PapersView.vue'), meta: { title: '试卷选题' } },
    { path: '/papers/:id', name: 'paper-detail', component: () => import('./views/PaperDetailView.vue'), props: true, meta: { title: '试卷详情' } },
    { path: '/paper', name: 'paper', component: () => import('./views/PaperView.vue'), meta: { title: '试卷编辑' } },
    { path: '/upload', name: 'upload', component: () => import('./views/UploadView.vue'), meta: { title: '试卷解析', staff: true } },
    { path: '/upload/knowledge', name: 'knowledge', component: () => import('./views/KnowledgeView.vue'), meta: { title: '知识树管理', staff: true } },
    { path: '/upload/eval', name: 'eval', component: () => import('./views/EvalView.vue'), meta: { title: '解析评测', admin: true } },
    // 管理后台：独立的邮箱账号登录，按模块授权（meta.permission），root 管理后台账号（meta.root）
    { path: '/admin/login', name: 'admin-login', component: () => import('./views/admin/AdminLoginView.vue'), meta: { title: '管理后台登录', console: true, public: true } },
    {
      path: '/admin',
      component: () => import('./views/admin/AdminLayout.vue'),
      meta: { console: true },
      children: [
        { path: '', name: 'admin', component: () => import('./views/admin/AdminOverview.vue'), meta: { title: '概览' } },
        { path: 'exams', name: 'admin-exams', component: () => import('./views/admin/AdminExams.vue'), meta: { title: '试卷管理', permission: 'exams' } },
        { path: 'exams/:id', name: 'admin-exam', component: () => import('./views/admin/AdminExamDetail.vue'), props: true, meta: { title: '试卷详情', permission: 'exams' } },
        { path: 'users', name: 'admin-users', component: () => import('./views/admin/AdminUsers.vue'), meta: { title: '用户与在线状态', permission: 'users' } },
        { path: 'keys', name: 'admin-keys', component: () => import('./views/admin/AdminKeys.vue'), meta: { title: '密钥配置', permission: 'keys' } },
        { path: 'pricing', name: 'admin-pricing', component: () => import('./views/admin/AdminPricing.vue'), meta: { title: '模型单价', permission: 'ai' } },
        { path: 'usage', name: 'admin-usage', component: () => import('./views/admin/AdminUsage.vue'), meta: { title: 'AI 用量', permission: 'ai' } },
        { path: 'accounts', name: 'admin-accounts', component: () => import('./views/admin/AdminAccounts.vue'), meta: { title: '后台账号', root: true } },
        { path: 'audit', name: 'admin-audit', component: () => import('./views/admin/AdminAudit.vue'), meta: { title: '操作记录', root: true } },
        { path: ':pathMatch(.*)*', redirect: '/admin' },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async to => {
  if (to.meta.console) {
    const c = useConsoleStore()
    if (!c.checked || !c.account) await c.restore()
    if (!c.account && !to.meta.public) return { path: '/admin/login', query: { redirect: to.fullPath } }
    if (c.account && to.name === 'admin-login') return '/admin'
    if ((to.meta.root && !c.isRoot) || (to.meta.permission && !c.can(to.meta.permission as Permission))) return '/admin'
    return
  }
  const auth = useAuthStore()
  await auth.restore()
  if (!auth.user && !to.meta.public) return { path: "/login", query: { redirect: to.fullPath } }
  if (auth.user && to.path === "/login") return "/"
  if ((to.meta.admin && !auth.isAdmin) || (to.meta.staff && !auth.isStaff)) return "/"
})

router.afterEach((to) => {
  const site = to.meta.console ? '福格云上题库管理后台' : '福格云上题库'
  document.title = to.meta.title ? `${to.meta.title} · ${site}` : site
})
