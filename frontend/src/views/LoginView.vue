<script setup lang="ts">
// 登录视图 - 邮箱+密码（+可选 TOTP），预填 demo 账号
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

// 预填 demo 账号（W6 演示）
const email = ref('admin@hra.demo')
const password = ref('admin123')
const totpCode = ref('')
const loading = ref(false)
const errorMsg = ref('')

async function handleLogin() {
  loading.value = true
  errorMsg.value = ''
  try {
    await auth.login(email.value, password.value, totpCode.value || undefined)
    const redirect = (route.query.redirect as string) || '/dashboard'
    router.push(redirect)
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    errorMsg.value = err?.response?.data?.detail || '登录失败，请检查邮箱与密码'
  } finally {
    loading.value = false
  }
}

// 一键填入 demo 账号（防止用户清空后无法演示）
function fillDemo() {
  email.value = 'admin@hra.demo'
  password.value = 'admin123'
  totpCode.value = ''
}
</script>

<template>
  <div class="login-container">
    <div class="login-card">
      <div class="brand">
        <div class="brand-icon">🎯</div>
        <h1>HRA</h1>
        <p class="subtitle">企业员工离职风险与人才流失预警系统</p>
      </div>
      <form @submit.prevent="handleLogin">
        <div class="form-item">
          <label>邮箱</label>
          <input v-model="email" type="email" required placeholder="user@example.com" autocomplete="username" />
        </div>
        <div class="form-item">
          <label>密码</label>
          <input v-model="password" type="password" required placeholder="••••••••" autocomplete="current-password" />
        </div>
        <div class="form-item">
          <label>2FA 验证码（管理员可选）</label>
          <input v-model="totpCode" type="text" maxlength="6" placeholder="6 位数字" autocomplete="one-time-code" />
        </div>
        <p v-if="errorMsg" class="error">⚠ {{ errorMsg }}</p>
        <button type="submit" :disabled="loading">
          {{ loading ? '登录中...' : '登录' }}
        </button>
      </form>
      <div class="demo-tip">
        <span>演示账号：</span>
        <code>admin@hra.demo</code> / <code>admin123</code>
        <button class="link-btn" @click="fillDemo">填入</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-container {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e293b 0%, #2563eb 100%);
  padding: 16px;
}
.login-card {
  background: white;
  padding: 40px;
  border-radius: 12px;
  width: 100%;
  max-width: 400px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
}
.brand {
  text-align: center;
  margin-bottom: 28px;
}
.brand-icon {
  font-size: 40px;
  margin-bottom: 8px;
}
h1 {
  font-size: 26px;
  letter-spacing: 2px;
  margin-bottom: 6px;
}
.subtitle {
  color: var(--color-text-muted);
  font-size: 12px;
}
.form-item {
  margin-bottom: 16px;
}
label {
  display: block;
  font-size: 13px;
  color: var(--color-text-muted);
  margin-bottom: 6px;
}
input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: 14px;
}
button {
  width: 100%;
  margin-top: 8px;
  padding: 12px;
  font-size: 15px;
}
.error {
  color: var(--color-danger);
  font-size: 13px;
  margin: 8px 0 4px;
  background: #fef2f2;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid #fecaca;
}
.demo-tip {
  margin-top: 20px;
  padding: 12px;
  background: #f8fafc;
  border-radius: 6px;
  font-size: 12px;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.demo-tip code {
  background: #e2e8f0;
  padding: 1px 6px;
  border-radius: 3px;
  color: var(--color-text);
}
.link-btn {
  background: transparent;
  color: var(--color-primary);
  padding: 0;
  margin: 0;
  margin-left: 6px;
  font-size: 12px;
  width: auto;
}
.link-btn:hover {
  text-decoration: underline;
  opacity: 1;
}
</style>
