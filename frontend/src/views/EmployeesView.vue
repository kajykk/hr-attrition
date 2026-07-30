<script setup lang="ts">
// 员工管理视图 - 列表表格 + 搜索 + 分页 + 风险色块，点击跳转风险预测
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { apiClient } from '@/api/client'
import type { EmployeeListItem, Paginated } from '@/api/types'

const router = useRouter()

const items = ref<EmployeeListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const errorMsg = ref('')
const searchKeyword = ref('')

// 演示模式标记
const demoMode = ref(false)

const levelLabels: Record<string, string> = {
  low: '低',
  medium_low: '中低',
  medium: '中',
  medium_high: '中高',
  high: '高',
}

// 前端过滤（按工号 / 脱敏姓名）
const filteredItems = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) return items.value
  return items.value.filter(
    (e) =>
      e.employee_no.toLowerCase().includes(kw) ||
      (e.name_masked || '').toLowerCase().includes(kw) ||
      (e.department_name || '').toLowerCase().includes(kw)
  )
})

async function fetchEmployees() {
  loading.value = true
  errorMsg.value = ''
  try {
    const { data } = await apiClient.get<Paginated<EmployeeListItem>>('/api/v1/employees', {
      params: { page: page.value, page_size: pageSize.value },
    })
    items.value = data.items
    total.value = data.total
    demoMode.value = false
  } catch (e: unknown) {
    const err = e as { message?: string }
    errorMsg.value = err?.message || '员工列表加载失败，已切换演示模式'
    demoMode.value = true
    fillDemo()
  } finally {
    loading.value = false
  }
}

function fillDemo() {
  items.value = [
    { id: 'emp-001', employee_no: 'EMP001', name_masked: '张*三', department_name: '研发中心', position: '高级工程师', status: 'active', risk_score: 87, risk_level: 'high', updated_at: new Date().toISOString() },
    { id: 'emp-042', employee_no: 'EMP042', name_masked: '李*华', department_name: '市场部', position: '市场专员', status: 'active', risk_score: 64, risk_level: 'medium_high', updated_at: new Date().toISOString() },
    { id: 'emp-118', employee_no: 'EMP118', name_masked: '王*芳', department_name: '财务部', position: '会计', status: 'active', risk_score: 48, risk_level: 'medium', updated_at: new Date().toISOString() },
    { id: 'emp-203', employee_no: 'EMP203', name_masked: '赵*伟', department_name: '人力资源部', position: 'HRBP', status: 'active', risk_score: 28, risk_level: 'medium_low', updated_at: new Date().toISOString() },
    { id: 'emp-317', employee_no: 'EMP317', name_masked: '陈*静', department_name: '研发中心', position: '测试工程师', status: 'active', risk_score: 12, risk_level: 'low', updated_at: new Date().toISOString() },
  ]
  total.value = items.value.length
}

function goToRisk(emp: EmployeeListItem) {
  router.push({ path: '/risk', query: { employee_id: emp.id } })
}

function prevPage() {
  if (page.value > 1) {
    page.value--
    fetchEmployees()
  }
}
function nextPage() {
  if (page.value * pageSize.value < total.value) {
    page.value++
    fetchEmployees()
  }
}

function fmtTime(s: string) {
  if (!s) return '-'
  try {
    return new Date(s).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return s
  }
}

function statusLabel(s: string) {
  const map: Record<string, string> = {
    active: '在职',
    onboarding: '入职中',
    leaving: '离职流程',
    left: '已离职',
  }
  return map[s] || s
}

onMounted(fetchEmployees)
</script>

<template>
  <div class="page">
    <h2 class="page-title">员工管理</h2>
    <p class="page-desc">PII 字段脱敏展示，点击行进入风险预测（D05 3.2 GET /employees）</p>

    <div v-if="demoMode" class="banner warning">⚠ {{ errorMsg }}</div>

    <div class="toolbar">
      <input
        v-model="searchKeyword"
        placeholder="按工号 / 姓名 / 部门搜索"
        class="search-input"
      />
      <button class="secondary" @click="fetchEmployees" :disabled="loading">
        {{ loading ? '加载中...' : '刷新' }}
      </button>
    </div>

    <div class="card">
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>工号</th>
              <th>姓名(脱敏)</th>
              <th>部门</th>
              <th>岗位</th>
              <th>状态</th>
              <th>风险分</th>
              <th>风险等级</th>
              <th>更新时间</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="emp in filteredItems"
              :key="emp.id"
              class="clickable"
              @click="goToRisk(emp)"
            >
              <td>{{ emp.employee_no }}</td>
              <td>{{ emp.name_masked }}</td>
              <td>{{ emp.department_name || '-' }}</td>
              <td>{{ emp.position || '-' }}</td>
              <td>{{ statusLabel(emp.status) }}</td>
              <td>
                <span v-if="emp.risk_score !== null" :class="'risk-' + emp.risk_level">
                  {{ emp.risk_score }}
                </span>
                <span v-else>-</span>
              </td>
              <td>
                <span
                  v-if="emp.risk_level"
                  class="risk-chip"
                  :class="emp.risk_level"
                >
                  {{ levelLabels[emp.risk_level] || emp.risk_level }}
                </span>
                <span v-else>-</span>
              </td>
              <td>{{ fmtTime(emp.updated_at) }}</td>
            </tr>
            <tr v-if="filteredItems.length === 0">
              <td colspan="8" class="empty">无匹配员工数据</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 分页 -->
      <div class="pagination">
        <span class="page-info">第 {{ page }} 页 / 共 {{ Math.max(1, Math.ceil(total / pageSize)) }} 页（合计 {{ total }} 条）</span>
        <div class="page-btns">
          <button class="secondary" :disabled="page <= 1 || loading" @click="prevPage">上一页</button>
          <button class="secondary" :disabled="page * pageSize >= total || loading" @click="nextPage">下一页</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.search-input {
  flex: 1;
  min-width: 240px;
  max-width: 400px;
}
.card {
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 16px;
}
.pagination {
  margin-top: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
  color: var(--color-text-muted);
}
.page-btns {
  display: flex;
  gap: 8px;
}
</style>
