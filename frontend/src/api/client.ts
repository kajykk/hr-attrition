// axios 实例 + 拦截器：注入 token / 401 静默刷新（refresh token 队列重放）
import axios, {
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from 'axios'
import {
  clearAuthStorage,
  getAccessToken,
  getRefreshToken,
  getStoredUser,
} from '@/api/auth-keys'

export const apiClient: AxiosInstance = axios.create({
  baseURL: '',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截：注入 Authorization + X-Tenant-Id（双保险，与 JWT 解析一致）
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = getAccessToken()
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
    }
    const user = getStoredUser<{ tenant_id?: string }>()
    if (user?.tenant_id && config.headers) {
      config.headers['X-Tenant-Id'] = user.tenant_id
    }
    return config
  },
  (error) => Promise.reject(error)
)

// ===== 401 静默刷新：refresh_token 换新 access_token 后重放原请求 =====
let refreshing: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const refresh = getRefreshToken()
  if (!refresh) return null
  try {
    const { data } = await axios.post<{ access_token: string }>('/api/v1/auth/refresh', {
      refresh_token: refresh,
    })
    localStorage.setItem('hra_token', data.access_token)
    return data.access_token
  } catch {
    return null
  }
}

function redirectToLogin(): void {
  if (window.location.pathname === '/login') return
  const redirect = window.location.pathname + window.location.search
  window.location.assign('/login?redirect=' + encodeURIComponent(redirect))
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error?.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined
    const status = error?.response?.status
    const url = original?.url || ''
    const isAuthRequest =
      url.includes('/auth/login') || url.includes('/auth/refresh')

    if (status === 401 && original && !original._retry && !isAuthRequest) {
      original._retry = true
      refreshing = refreshing || refreshAccessToken()
      const newToken = await refreshing
      refreshing = null
      if (newToken) {
        original.headers.Authorization = `Bearer ${newToken}`
        return apiClient(original)
      }
      // 刷新失败：登出并跳登录页（保留回跳地址）
      clearAuthStorage()
      redirectToLogin()
    }
    return Promise.reject(error)
  }
)

// 供 SSE 等非 axios 请求复用 token/租户头
export function buildAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const user = getStoredUser<{ tenant_id?: string }>()
  if (user?.tenant_id) headers['X-Tenant-Id'] = user.tenant_id
  return headers
}

// 统一错误提取（各视图共用，避免手写类型断言）
export function extractApiError(e: unknown, fallback = '请求失败'): string {
  const err = e as { response?: { data?: { detail?: string } }; message?: string }
  return err?.response?.data?.detail || err?.message || fallback
}
