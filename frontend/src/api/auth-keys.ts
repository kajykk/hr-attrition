// 认证存储 key 与读写工具（统一维护 localStorage 键，避免散落各处）
//
// 安全策略（HttpOnly Cookie 改造）：refresh token 不再新写入 localStorage
// （服务端经 HttpOnly Cookie 下发，JS 不可读）；getRefreshToken 仅用于读取
// 历史会话的遗留值作为刷新请求体回退，登录后即被 cookie 流程取代。
export const AUTH_KEYS = {
  token: 'hra_token',
  refreshToken: 'hra_refresh_token',
  user: 'hra_user',
} as const

export function getAccessToken(): string {
  return localStorage.getItem(AUTH_KEYS.token) || ''
}

/** 遗留 refresh token（旧版本会话）；新会话恒为空（凭据走 HttpOnly Cookie） */
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

export function setAuthStorage(accessToken: string, refreshToken: string | null | undefined, user: unknown): void {
  localStorage.setItem(AUTH_KEYS.token, accessToken)
  if (refreshToken) {
    // 仅旧版后端（未启用 cookie）才会提供；新版保持存储清洁
    localStorage.setItem(AUTH_KEYS.refreshToken, refreshToken)
  } else {
    localStorage.removeItem(AUTH_KEYS.refreshToken)
  }
  localStorage.setItem(AUTH_KEYS.user, JSON.stringify(user))
}

export function clearAuthStorage(): void {
  localStorage.removeItem(AUTH_KEYS.token)
  localStorage.removeItem(AUTH_KEYS.refreshToken)
  localStorage.removeItem(AUTH_KEYS.user)
}
