// axios 实例 + 拦截器注入 tenant_id / token（D05 2.1 + ADR-002 租户隔离）
import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'

export const apiClient: AxiosInstance = axios.create({
  baseURL: '',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截：注入 Authorization + tenant_id
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('hra_token')
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
    }
    // 从 user 信息注入 X-Tenant-Id（双保险，与 JWT 解析一致）
    const userStr = localStorage.getItem('hra_user')
    if (userStr) {
      try {
        const user = JSON.parse(userStr)
        if (user?.tenant_id && config.headers) {
          config.headers['X-Tenant-Id'] = user.tenant_id
        }
      } catch {
        /* ignore */
      }
    }
    return config
  },
  (error) => Promise.reject(error)
)

// 响应拦截：401 自动登出
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem('hra_token')
      localStorage.removeItem('hra_refresh_token')
      localStorage.removeItem('hra_user')
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)
