// @vitest-environment jsdom
// auth store 单测：登录持久化 / HttpOnly Cookie 模式（无 body refresh token）/ 登出吊销
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const postMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', () => ({
  apiClient: {
    post: (...args: unknown[]) => postMock(...args),
  },
}))

import { useAuthStore } from '@/stores/auth'

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    postMock.mockReset()
    // 默认成功响应（logout 为 fire-and-forget，也依赖返回 Promise）
    postMock.mockResolvedValue({ data: {} })
  })

  it('login 成功：token/user 写入 store 与 localStorage', async () => {
    postMock.mockResolvedValue({
      data: {
        access_token: 'acc-1',
        refresh_token: 'ref-1',
        expires_in: 1800,
        user: { id: 'u1', name: '管理员', role: 'admin', tenant_id: 't1', email: 'a@b.c' },
      },
    })
    const store = useAuthStore()
    await store.login('a@b.c', 'pw')

    expect(store.token).toBe('acc-1')
    expect(store.refreshToken).toBe('ref-1')
    expect(store.user?.role).toBe('admin')
    expect(localStorage.getItem('hra_token')).toBe('acc-1')
    expect(JSON.parse(localStorage.getItem('hra_user') || '{}').id).toBe('u1')
  })

  it('HttpOnly Cookie 模式：refresh_token 为空时不落 localStorage，store 留空串', async () => {
    postMock.mockResolvedValue({
      data: {
        access_token: 'acc-2',
        refresh_token: null,
        expires_in: 1800,
        user: { id: 'u2', name: 'HR', role: 'hr_manager', tenant_id: 't1', email: 'h@b.c' },
      },
    })
    const store = useAuthStore()
    await store.login('h@b.c', 'pw')

    expect(store.refreshToken).toBe('')
    expect(localStorage.getItem('hra_refresh_token')).toBeNull()
  })

  it('logout：清空本地状态与存储，并 fire-and-forget 调用服务端吊销', () => {
    const store = useAuthStore()
    store.token = 'acc-3'
    store.user = { id: 'u3', name: 'x', role: 'admin', tenant_id: 't1', email: 'x@y.z' }
    localStorage.setItem('hra_token', 'acc-3')
    localStorage.setItem('hra_user', '{}')

    store.logout()

    expect(postMock).toHaveBeenCalledWith('/api/v1/auth/logout')
    expect(store.token).toBe('')
    expect(store.user).toBeNull()
    expect(localStorage.getItem('hra_token')).toBeNull()
    expect(localStorage.getItem('hra_user')).toBeNull()
  })
})
