// @vitest-environment jsdom
// AppLayout 单测：菜单按角色过滤（最小知情）/ 侧边栏折叠持久化
import { describe, expect, it, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import NotFoundView from '@/views/NotFoundView.vue'
import { useAuthStore } from '@/stores/auth'

function makeRouter() {
  // 挂载用最小路由（避免真实路由表懒加载全部视图）
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/:pathMatch(.*)*', name: 'any', component: NotFoundView },
    ],
  })
}

function mountLayout() {
  const router = makeRouter()
  return mount(AppLayout, {
    global: {
      plugins: [router],
      stubs: { RouterView: true, RouterLink: { template: '<a><slot /></a>' } },
    },
  })
}

describe('AppLayout', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('employee 仅见无角色限制菜单（不含保留建议/治理）', () => {
    const store = useAuthStore()
    store.token = 't'
    store.user = { id: 'u', name: '员工', role: 'employee', tenant_id: 't1', email: 'e@b.c' }
    const wrapper = mountLayout()
    const labels = wrapper.findAll('nav span').map((n) => n.text())
    expect(labels).toContain('仪表盘')
    expect(labels).not.toContain('保留建议')
    expect(labels).not.toContain('治理')
  })

  it('admin 可见全部 7 个菜单项', () => {
    const store = useAuthStore()
    store.token = 't'
    store.user = { id: 'u', name: '管理员', role: 'admin', tenant_id: 't1', email: 'a@b.c' }
    const wrapper = mountLayout()
    const labels = wrapper.findAll('nav span').map((n) => n.text())
    for (const expected of ['仪表盘', '员工', '风险预测', '预警', '保留建议', '知识库', '治理']) {
      expect(labels).toContain(expected)
    }
  })

  it('折叠切换按钮更新状态并持久化到 localStorage', async () => {
    const wrapper = mountLayout()
    // 展开态点击收起按钮（title=收起）→ collapsed=true 并写入 '1'
    const toggleBtn = wrapper.find('button[title="收起"], button[title="展开"]')
    expect(toggleBtn.exists()).toBe(true)
    await toggleBtn.trigger('click')
    expect(localStorage.getItem('hra_sidebar_collapsed')).toBe('1')
    await toggleBtn.trigger('click')
    expect(localStorage.getItem('hra_sidebar_collapsed')).toBe('0')
  })
})
