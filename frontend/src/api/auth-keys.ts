// 认证存储 key 与读写工具（统一维护 localStorage 键，避免散落各处）
export const AUTH_KEYS = {
  token: 'hra_token',
  refreshToken: 'hra_refresh_token',
  user: 'hra_user',
} as const

export function getAccessToken(): string {
  return localStorage.getItem(AUTH_KEYS.token) || ''
}

export function getRefreshToken(): string {
  return localStorage.getItem(AUTH_KEYS.refreshToken) || ''
}

export function getStoredUser<T>(): T | null {
  const raw = localStorage.getItem(AUTH_KEYS.user)
  if (!raw) return null
  try {
    return JSON.parse(raw) as T
  } catch {
    // 脏数据：清除避免应用崩溃
    clearAuthStorage()
    return null
  }
}

export function setAuthStorage(accessToken: string, refreshToken: string, user: unknown): void {
  localStorage.setItem(AUTH_KEYS.token, accessToken)
  localStorage.setItem(AUTH_KEYS.refreshToken, refreshToken)
  localStorage.setItem(AUTH_KEYS.user, JSON.stringify(user))
}

export function clearAuthStorage(): void {
  localStorage.removeItem(AUTH_KEYS.token)
  localStorage.removeItem(AUTH_KEYS.refreshToken)
  localStorage.removeItem(AUTH_KEYS.user)
}
