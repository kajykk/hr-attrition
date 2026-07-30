// 认证 store - token / user / login / logout（D05 3.1）
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiClient } from '@/api/client'

interface UserInfo {
  id: string
  name: string
  role: string
  tenant_id: string
  email: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('hra_token') || '')
  const refreshToken = ref<string>(localStorage.getItem('hra_refresh_token') || '')
  const user = ref<UserInfo | null>(
    JSON.parse(localStorage.getItem('hra_user') || 'null')
  )

  const isLoggedIn = computed(() => !!token.value)

  function setAuth(accessToken: string, refresh: string, userInfo: UserInfo) {
    token.value = accessToken
    refreshToken.value = refresh
    user.value = userInfo
    localStorage.setItem('hra_token', accessToken)
    localStorage.setItem('hra_refresh_token', refresh)
    localStorage.setItem('hra_user', JSON.stringify(userInfo))
  }

  async function login(email: string, password: string, totpCode?: string) {
    const { data } = await apiClient.post('/api/v1/auth/login', {
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
    localStorage.removeItem('hra_token')
    localStorage.removeItem('hra_refresh_token')
    localStorage.removeItem('hra_user')
  }

  return { token, refreshToken, user, isLoggedIn, setAuth, login, logout }
})
