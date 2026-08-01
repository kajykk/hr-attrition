// 认证 store - token / user / login / logout（D05 3.1）
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiClient } from '@/api/client'
import {
  clearAuthStorage,
  getAccessToken,
  getRefreshToken,
  getStoredUser,
  setAuthStorage,
} from '@/api/auth-keys'

export interface UserInfo {
  id: string
  name: string
  role: string
  tenant_id: string
  email: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(getAccessToken())
  const refreshToken = ref<string>(getRefreshToken())
  const user = ref<UserInfo | null>(getStoredUser<UserInfo>())

  const isLoggedIn = computed(() => !!token.value)

  function setAuth(accessToken: string, refresh: string, userInfo: UserInfo) {
    token.value = accessToken
    refreshToken.value = refresh
    user.value = userInfo
    setAuthStorage(accessToken, refresh, userInfo)
  }

  async function login(email: string, password: string, totpCode?: string) {
    const { data } = await apiClient.post<{
      access_token: string
      refresh_token: string
      user: UserInfo
    }>('/api/v1/auth/login', {
      email,
      password,
      totp_code: totpCode,
    })
    setAuth(data.access_token, data.refresh_token, data.user)
    return data
  }

  function logout() {
    token.value = ''
    refreshToken.value = ''
    user.value = null
    clearAuthStorage()
  }

  return { token, refreshToken, user, isLoggedIn, setAuth, login, logout }
})
