// 路由配置 - 7 个核心路由 + 404 + 角色守卫（D05 端点对应）
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { endRouteDwell, startRouteDwell } from '@/utils/tracker'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/components/AppLayout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'employees', name: 'employees', component: () => import('@/views/EmployeesView.vue'), meta: { title: '员工管理' } },
      { path: 'risk', name: 'risk', component: () => import('@/views/RiskView.vue'), meta: { title: '风险预测' } },
      { path: 'warnings', name: 'warnings', component: () => import('@/views/WarningsView.vue'), meta: { title: '预警中心' } },
      {
        path: 'advise',
        name: 'advise',
        component: () => import('@/views/AdviseView.vue'),
        meta: { title: 'AI 保留建议', roles: ['admin', 'hr_manager', 'hrbp'] },
      },
      {
        path: 'knowledge',
        name: 'knowledge',
        component: () => import('@/views/KnowledgeBaseView.vue'),
        meta: { title: '制度知识库' },
      },
      { path: 'dashboard', name: 'dashboard', component: () => import('@/views/DashboardView.vue'), meta: { title: '仪表盘' } },
      {
        path: 'governance',
        name: 'governance',
        component: () => import('@/views/GovernanceView.vue'),
        meta: { title: '模型治理', roles: ['admin'] },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { public: true, title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

const TITLE_PREFIX = 'HRA - 离职风险预警'

// 守卫：登录校验 + 角色校验 + 已登录访问 /login 重定向
router.beforeEach((to) => {
  const auth = useAuthStore()

  if (!to.meta.public && !auth.token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && auth.token) {
    return { name: 'dashboard' }
  }
  const roles = to.meta.roles as string[] | undefined
  // 角色受限页要求用户信息可用：token 存在但 user 缺失（存储损坏/未初始化）
  // 时同样拒绝，防止守卫被整体跳过直达 admin 页
  if (roles && (!auth.user || !roles.includes(auth.user.role))) {
    return { name: 'dashboard' }
  }

  const title = (to.meta.title as string) || ''
  document.title = title ? `${TITLE_PREFIX} | ${title}` : TITLE_PREFIX

  // 行为埋点：路由进入启动页面停留计时（登录页/public 页不埋点）
  if (!to.meta.public) {
    startRouteDwell(String(to.name || 'anonymous'), to.fullPath)
  }
})

// 路由离开：结算页面停留并上报（nextTick 保证目标路由生效后再结算）
router.afterEach(() => {
  endRouteDwell()
})

// 页面可见性：切后台暂停计时、回前台恢复（移动端切应用/切标签页）
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      // 需要 import 的 pause/resume
      void import('@/utils/tracker').then(({ pauseRouteDwell }) => pauseRouteDwell())
    } else {
      void import('@/utils/tracker').then(({ resumeRouteDwell }) => resumeRouteDwell())
    }
  })
}

export default router
