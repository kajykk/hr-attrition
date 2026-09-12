// 认证 store - token / user / login / logout（D05 3.1）
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiClient } from '@/api/client'
import type { UserInfo } from '@/api/types'
import {
  clearAuthStorage,
  getAccessToken,
  getRefreshToken,
  getStoredUser,
  setAuthStorage,
} from '@/api/auth-keys'

// UserInfo 单一定义在 api/types.ts（对齐后端 UserOut），此处仅复用
export type { UserInfo }

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(getAccessToken())
  const refreshToken = ref<string>(getRefreshToken())
  const user = ref<UserInfo | null>(getStoredUser<UserInfo>())

  const isLoggedIn = computed(() => !!token.value)

  function setAuth(accessToken: string, refresh: string | null | undefined, userInfo: UserInfo) {
    token.value = accessToken
    refreshToken.value = refresh || ''
    user.value = userInfo
    setAuthStorage(accessToken, refresh, userInfo)
  }

  async function login(email: string, password: string, totpCode?: string) {
    const { data } = await apiClient.post<{
      access_token: string
      refresh_token?: string | null
      user: UserInfo
    }>('/api/v1/auth/login', {
      email,
      password,
      totp_code: totpCode,
    })
    // 新版后端经 HttpOnly Cookie 下发 refresh token（data.refresh_token 为空）
    setAuth(data.access_token, data.refresh_token, data.user)
    return data
  }

  function logout() {
    // 吊销服务端 HttpOnly Cookie 中的 refresh jti（fire-and-forget，失败不阻塞本地登出）
    void apiClient.post('/api/v1/auth/logout').catch(() => {})
    token.value = ''
    refreshToken.value = ''
    user.value = null
    clearAuthStorage()
  }

  return { token, refreshToken, user, isLoggedIn, setAuth, login, logout }
})
