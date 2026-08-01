<script setup lang="ts">
// 应用布局 - 左侧导航 + 顶部用户栏 + 主内容区
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { RouterView, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

// 菜单按角色过滤（治理页仅管理员，保留建议仅 HR 角色）
const allMenu = [
  { path: '/dashboard', label: '仪表盘', icon: '📊', roles: null as string[] | null },
  { path: '/employees', label: '员工', icon: '👥', roles: null },
  { path: '/risk', label: '风险预测', icon: '⚠️', roles: null },
  { path: '/warnings', label: '预警', icon: '🚨', roles: null },
  { path: '/advise', label: '保留建议', icon: '🤖', roles: ['admin', 'hr_manager', 'hrbp'] },
  { path: '/governance', label: '治理', icon: '⚙️', roles: ['admin'] },
]

const menu = computed(() => {
  const role = auth.user?.role
  if (!role) return []
  return allMenu.filter((m) => !m.roles || m.roles.includes(role))
})

const collapsed = ref(localStorage.getItem('hra_sidebar_collapsed') === '1')
const isNarrow = ref(false)

function handleResize() {
  isNarrow.value = window.innerWidth < 768
  if (isNarrow.value) collapsed.value = true
}

onMounted(() => {
  handleResize()
  window.addEventListener('resize', handleResize)
})
onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})

function toggleSidebar() {
  collapsed.value = !collapsed.value
  localStorage.setItem('hra_sidebar_collapsed', collapsed.value ? '1' : '0')
}

function handleLogout() {
  auth.logout()
  router.push('/login')
}

function roleLabel(role: string) {
  const map: Record<string, string> = {
    admin: '系统管理员',
    hr_manager: 'HR 经理',
    hrbp: 'HR BP',
    manager: '直线经理',
    employee: '员工',
  }
  return map[role] || role
}
</script>

<template>
  <div
    class="layout"
    :class="{ collapsed }"
  >
    <aside class="sidebar">
      <div class="logo">
        <span class="logo-icon">🎯</span>
        <span
          v-if="!collapsed"
          class="logo-text"
        >HRA 离职风险预警</span>
      </div>
      <nav>
        <RouterLink
          v-for="item in menu"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :title="item.label"
        >
          <span class="icon">{{ item.icon }}</span>
          <span
            v-if="!collapsed"
            class="label"
          >{{ item.label }}</span>
        </RouterLink>
      </nav>
    </aside>
    <main class="main">
      <header class="header">
        <div class="header-left">
          <button
            class="toggle-btn secondary"
            :title="collapsed ? '展开' : '收起'"
            @click="toggleSidebar"
          >
            {{ collapsed ? '☰' : '✕' }}
          </button>
          <div class="header-title">
            企业员工离职风险与人才流失预警系统
          </div>
        </div>
        <div class="header-user">
          <span
            v-if="auth.user"
            class="user-info"
          >
            <span class="user-name">{{ auth.user.name }}</span>
            <span class="user-role">{{ roleLabel(auth.user.role) }}</span>
          </span>
          <button
            class="secondary"
            @click="handleLogout"
          >
            退出
          </button>
        </div>
      </header>
      <div class="content">
        <RouterView />
      </div>
    </main>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
}
.sidebar {
  width: var(--sidebar-width);
  background: #1e293b;
  color: white;
  padding: 16px 0;
  flex-shrink: 0;
  transition: width 0.2s;
  overflow: hidden;
}
.layout.collapsed .sidebar {
  width: 60px;
}
.logo {
  padding: 0 20px 20px;
  font-size: 15px;
  font-weight: 600;
  border-bottom: 1px solid #334155;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.layout.collapsed .logo {
  padding: 0 0 20px;
  justify-content: center;
}
.logo-icon {
  font-size: 18px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  color: #cbd5e1;
  text-decoration: none;
  transition: background 0.2s;
  white-space: nowrap;
}
.layout.collapsed .nav-item {
  justify-content: center;
  padding: 10px 0;
}
.nav-item:hover,
.nav-item.router-link-active {
  background: #334155;
  color: white;
  border-left: 3px solid var(--color-primary);
  padding-left: 17px;
}
.layout.collapsed .nav-item:hover,
.layout.collapsed .nav-item.router-link-active {
  padding-left: 0;
  border-left: none;
  border-right: 3px solid var(--color-primary);
}
.icon {
  font-size: 16px;
}
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}
.header {
  height: var(--header-height);
  background: var(--color-card);
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
  gap: 12px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.toggle-btn {
  padding: 6px 10px;
  font-size: 14px;
}
.header-title {
  font-size: 15px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-user {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.user-info {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  line-height: 1.2;
}
.user-name {
  font-weight: 600;
  color: var(--color-text);
}
.user-role {
  font-size: 11px;
}
.content {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
  overflow-x: hidden;
}

@media (max-width: 640px) {
  .header-title { display: none; }
  .header { padding: 0 12px; }
  .content { padding: 12px; }
  .user-role { display: none; }
}
</style>
