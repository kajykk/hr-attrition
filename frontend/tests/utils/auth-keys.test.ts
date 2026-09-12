// auth-keys 单测：key 常量 / token 读写 / 脏数据清理 / refresh 回退语义
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  AUTH_KEYS,
  clearAuthStorage,
  getAccessToken,
  getRefreshToken,
  getStoredUser,
  setAuthStorage,
} from '@/api/auth-keys'

// 使用虚拟 localStorage（测试环境不挂真实 localStorage）
function makeStorage(): Storage {
  let store = new Map<string, string>()
  return {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (k) => (store.has(k) ? store.get(k)! : null),
    key: (i) => [...store.keys()][i] ?? null,
    removeItem: (k) => void store.delete(k),
    setItem: (k, v) => void store.set(k, v),
  } as Storage
}

let storage: Storage
beforeEach(() => {
  storage = makeStorage()
  vi.stubGlobal('localStorage', storage)
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('AUTH_KEYS 常量', () => {
  it('暴露统一的存储键名（单一事实源）', () => {
    expect(AUTH_KEYS.token).toBe('hra_token')
    expect(AUTH_KEYS.refreshToken).toBe('hra_refresh_token')
    expect(AUTH_KEYS.user).toBe('hra_user')
  })
})

describe('getAccessToken / getRefreshToken', () => {
  it('无 token 时返回空串（不抛异常）', () => {
    expect(getAccessToken()).toBe('')
    expect(getRefreshToken()).toBe('')
  })

  it('从 localStorage 读取已存 token', () => {
    storage.setItem('hra_token', 'abc')
    storage.setItem('hra_refresh_token', 'legacy')
    expect(getAccessToken()).toBe('abc')
    expect(getRefreshToken()).toBe('legacy')
  })
})

describe('setAuthStorage', () => {
  it('写入 accessToken + user，refreshToken 缺失时保持存储清洁', () => {
    setAuthStorage('tok-1', null, { id: 1 })
    expect(getAccessToken()).toBe('tok-1')
    expect(getRefreshToken()).toBe('')
    expect(getStoredUser()).toEqual({ id: 1 })
  })

  it('兼容旧版后端提供 refreshToken（写入遗留键，供刷新回退）', () => {
    setAuthStorage('tok-1', 'rt-old', {})
    expect(getRefreshToken()).toBe('rt-old')
  })

  it('refreshToken 为空白时清除遗留 refresh 键', () => {
    storage.setItem('hra_refresh_token', 'should-clear')
    setAuthStorage('tok-new', undefined, {})
    expect(getRefreshToken()).toBe('')
  })
})

describe('getStoredUser / clearAuthStorage', () => {
  it('返回解析后的用户对象', () => {
    storage.setItem('hra_user', '{"name":"tester","role":"hr_manager"}')
    expect(getStoredUser()).toEqual({ name: 'tester', role: 'hr_manager' })
  })

  it('无存储用户时返回 null', () => {
    expect(getStoredUser()).toBeNull()
  })

  it('脏 JSON 时清除存储并返回 null（避免应用崩溃）', () => {
    storage.setItem('hra_user', '{broken json')
    expect(getStoredUser()).toBeNull()
    // 触发清理后 access/refresh 也被一并清除（保留存储一致性）
    expect(getAccessToken()).toBe('')
    expect(getRefreshToken()).toBe('')
  })

  it('clearAuthStorage 移除三个键', () => {
    setAuthStorage('tok', 'rt', { id: 1 })
    clearAuthStorage()
    expect(getAccessToken()).toBe('')
    expect(getRefreshToken()).toBe('')
    expect(getStoredUser()).toBeNull()
  })
})