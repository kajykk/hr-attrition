// 路由配置 - 7 个核心路由 + 404 + 角色守卫（D05 端点对应）
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

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
  if (roles && auth.user && !roles.includes(auth.user.role)) {
    return { name: 'dashboard' }
  }

  const title = (to.meta.title as string) || ''
  document.title = title ? `${TITLE_PREFIX} | ${title}` : TITLE_PREFIX
})

export default router
