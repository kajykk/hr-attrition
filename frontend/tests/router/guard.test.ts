// @vitest-environment jsdom
// 路由守卫单测：登录校验 / user 缺失时角色受限页拒绝 / 角色越权重定向
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'

function loginAs(role: string | null, withUser = true) {
  const store = useAuthStore()
  store.token = 'tok-' + (role ?? 'anon')
  if (withUser && role) {
    store.user = { id: 'u1', name: '测试', role, tenant_id: 't1', email: 'a@b.c' }
  }
}

describe('router guards', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('未登录访问受控页面 → 重定向 /login 并携带 redirect 回跳参数', async () => {
    await router.push('/employees')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/employees')
  })

  it('已登录访问 /login → 重定向 dashboard', async () => {
    loginAs('admin')
    // 先落在受控页，再访问 /login（避免重复导航不触发守卫）
    await router.replace('/dashboard')
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('dashboard')
  })

  it('token 存在但 user 缺失：admin 受限页拒绝（防守卫被跳过直达）', async () => {
    loginAs('admin', false) // 有 token、无用户信息（存储损坏场景）
    await router.push('/governance')
    expect(router.currentRoute.value.name).toBe('dashboard')
  })

  it('employee 访问 advise（HR 角色）→ 重定向 dashboard', async () => {
    loginAs('employee')
    await router.push('/advise')
    expect(router.currentRoute.value.name).toBe('dashboard')
  })

  it('admin 可进入 governance；无角色限制页对所有登录角色放行', async () => {
    loginAs('admin')
    await router.push('/governance')
    expect(router.currentRoute.value.name).toBe('governance')
    await router.push('/employees')
    expect(router.currentRoute.value.name).toBe('employees')
  })
})
