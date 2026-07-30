// 路由配置 - 7 个核心路由（D05 端点对应）
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('@/components/AppLayout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'employees', name: 'employees', component: () => import('@/views/EmployeesView.vue') },
      { path: 'risk', name: 'risk', component: () => import('@/views/RiskView.vue') },
      { path: 'warnings', name: 'warnings', component: () => import('@/views/WarningsView.vue') },
      { path: 'advise', name: 'advise', component: () => import('@/views/AdviseView.vue') },
      { path: 'dashboard', name: 'dashboard', component: () => import('@/views/DashboardView.vue') },
      { path: 'governance', name: 'governance', component: () => import('@/views/GovernanceView.vue') },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 守卫：未登录跳转 /login
router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
})

export default router
