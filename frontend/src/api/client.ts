// axios 实例 + 拦截器：注入 token / 401 静默刷新（refresh token 队列重放）
import axios, {
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from 'axios'
import {
  AUTH_KEYS,
  clearAuthStorage,
  getAccessToken,
  getRefreshToken,
} from '@/api/auth-keys'
import { useAuthStore } from '@/stores/auth'

// /auth/refresh 响应（轮换时后端会回发新的 refresh_token）
interface RefreshResponse {
  access_token: string
  refresh_token?: string
  expires_in: number
}

export const apiClient: AxiosInstance = axios.create({
  baseURL: '',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截：仅注入 Authorization（租户上下文由后端从 JWT 解析，客户端不可自报）
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = getAccessToken()
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
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
    // 后端实现 refresh 轮换：旧 token 进黑名单，响应携带新 refresh_token
    const { data } = await axios.post<RefreshResponse>('/api/v1/auth/refresh', {
      refresh_token: refresh,
    })
    localStorage.setItem(AUTH_KEYS.token, data.access_token)
    if (data.refresh_token) {
      localStorage.setItem(AUTH_KEYS.refreshToken, data.refresh_token)
    }
    // 同步 Pinia auth store（组件层读取的是 store.token，仅写 localStorage 会导致状态过期）
    try {
      const auth = useAuthStore()
      auth.token = data.access_token
      if (data.refresh_token) {
        auth.refreshToken = data.refresh_token
      }
    } catch {
      // pinia 未激活的极端场景：localStorage 已更新，不阻塞刷新流程
    }
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

// 供 SSE 等非 axios 请求复用 token（租户上下文由后端从 JWT 解析）
export function buildAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

// 统一错误提取（各视图共用，避免手写类型断言）
export function extractApiError(e: unknown, fallback = '请求失败'): string {
  const err = e as { response?: { data?: { detail?: string } }; message?: string }
  return err?.response?.data?.detail || err?.message || fallback
}
