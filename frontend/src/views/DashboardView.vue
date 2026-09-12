<script setup lang="ts">
import { fmtTime } from '@/utils/format'
// 仪表盘视图 - KPI 卡片 + 风险分布条形图 + 最近预警 + Kill Switch 状态 + 实时推送
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { apiClient } from '@/api/client'
import { getAccessToken } from '@/api/auth-keys'
import { useWebSocket, type RiskUpdatePayload } from '@/composables/useWebSocket'
import { useAuthStore } from '@/stores/auth'
import type { WarningOut, Paginated, KillSwitchStatus } from '@/api/types'

const auth = useAuthStore()

// 演示数据仅限开发环境；生产环境失败展示真实错误
const allowDemo = import.meta.env.DEV

// 触发演示占位数据的最低失败项数（DEV 环境，多数关键块失败才切换演示）
const DEMO_FAILURE_THRESHOLD = 4

interface KpiStats {
  totalEmployees: number
  highRiskCount: number
  pendingWarnings: number
  modelVersion: string
}

// 后端 /employees/stats/risk-distribution 响应（全量聚合口径）
interface RiskDistributionStats {
  total_employees: number
  high_risk_count: number
  distribution: Record<string, number>
}

const stats = ref<KpiStats>({
  totalEmployees: 0,
  highRiskCount: 0,
  pendingWarnings: 0,
  modelVersion: '-',
})

const distribution = ref<Array<{ level: string; count: number }>>([
  { level: 'low', count: 0 },
  { level: 'medium_low', count: 0 },
  { level: 'medium', count: 0 },
  { level: 'medium_high', count: 0 },
  { level: 'high', count: 0 },
])

const recentWarnings = ref<WarningOut[]>([])
const killSwitch = ref<KillSwitchStatus>({ active: false })
const demoMode = ref(false)
const loading = ref(false)
const errorMsg = ref('')

// 风险等级中文标签
const levelLabels: Record<string, string> = {
  low: '低风险',
  medium_low: '中低风险',
  medium: '中风险',
  medium_high: '中高风险',
  high: '高风险',
}
const levelColors: Record<string, string> = {
  low: 'var(--risk-low)',
  medium_low: 'var(--risk-medium_low)',
  medium: 'var(--risk-medium)',
  medium_high: 'var(--risk-medium_high)',
  high: 'var(--risk-high)',
}

// 条形图最大值（用于计算宽度）
const distMax = computed(() => Math.max(1, ...distribution.value.map((d) => d.count)))

async function loadDashboard() {
  loading.value = true
  errorMsg.value = ''
  const failures: string[] = []

  // Kill Switch 为 admin-only 接口：非管理员角色不请求（否则每次进首页必吃 403）
  const isAdmin = auth.user?.role === 'admin'

  const results = await Promise.allSettled([
    // 1. 员工总数 + 风险分布（后端全量聚合，不再受分页上限影响）
    apiClient.get<RiskDistributionStats>('/api/v1/employees/stats/risk-distribution'),
    // 2. 最近 5 条预警 + 待处理数（并行）
    apiClient.get<Paginated<WarningOut>>('/api/v1/warnings', { params: { page: 1, page_size: 5 } }),
    apiClient.get<Paginated<WarningOut>>('/api/v1/warnings', {
      params: { page: 1, page_size: 1, status: 'new' },
    }),
    // 3. Kill Switch 状态（仅管理员）
    isAdmin
      ? apiClient.get<KillSwitchStatus>('/api/v1/admin/kill-switch')
      : (Promise.resolve({ data: null }) as Promise<{ data: KillSwitchStatus | null }>),
    // 4. 模型版本（从全局解释接口取）
    apiClient.get<{ model_version: string }>('/api/v1/risk/global-explanation', {
      params: { window_days: 30 },
    }),
  ])
  const ok = (r: PromiseSettledResult<unknown>) => r.status === 'fulfilled'

  if (ok(results[0])) {
    const data = (results[0] as PromiseFulfilledResult<{ data: RiskDistributionStats }>).value.data
    stats.value.totalEmployees = data.total_employees
    stats.value.highRiskCount = data.high_risk_count
    distribution.value = Object.entries(data.distribution)
      .filter(([level]) => level !== 'unscored')
      .map(([level, count]) => ({ level, count }))
  } else {
    failures.push('员工统计')
  }
  if (ok(results[1])) {
    recentWarnings.value = (results[1] as PromiseFulfilledResult<{ data: Paginated<WarningOut> }>).value.data.items
  } else {
    failures.push('最近预警')
  }
  if (ok(results[2])) {
    stats.value.pendingWarnings = (results[2] as PromiseFulfilledResult<{ data: Paginated<WarningOut> }>).value.data.total
  } else {
    failures.push('待处理预警数')
  }
  if (isAdmin) {
    if (ok(results[3]) && (results[3] as PromiseFulfilledResult<{ data: KillSwitchStatus | null }>).value.data) {
      killSwitch.value = (results[3] as PromiseFulfilledResult<{ data: KillSwitchStatus }>).value.data
    } else {
      failures.push('Kill Switch 状态')
    }
  }
  if (ok(results[4])) {
    stats.value.modelVersion = (results[4] as PromiseFulfilledResult<{ data: { model_version: string } }>).value.data.model_version
  } else {
    failures.push('模型版本')
  }

  if (failures.length > 0) {
    errorMsg.value = `部分数据加载失败：${failures.join('、')}`
    // 演示数据仅限开发环境
    if (allowDemo && failures.length >= DEMO_FAILURE_THRESHOLD) {
      demoMode.value = true
      errorMsg.value += '（开发环境演示数据）'
      fillDemoPlaceholders()
    }
  }
  loading.value = false
}

// 演示模式占位数据
function fillDemoPlaceholders() {
  stats.value = {
    totalEmployees: 248,
    highRiskCount: 17,
    pendingWarnings: 12,
    modelVersion: 'fusion-v3.2',
  }
  distribution.value = [
    { level: 'low', count: 142 },
    { level: 'medium_low', count: 58 },
    { level: 'medium', count: 31 },
    { level: 'medium_high', count: 12 },
    { level: 'high', count: 5 },
  ]
  recentWarnings.value = [
    {
      id: 'w-demo-1',
      employee_id: 'emp-001',
      prediction_id: 'pred-001',
      level: 'P0',
      risk_score: 87,
      status: 'new',
      assigned_to: '李 HR',
      escalated_to: null,
      message: '高风险：连续 5 天未登录 + 加班时长激增',
      created_at: new Date(Date.now() - 3600 * 1000).toISOString(),
      confirmed_at: null,
      closed_at: null,
    },
    {
      id: 'w-demo-2',
      employee_id: 'emp-042',
      prediction_id: 'pred-042',
      level: 'P1',
      risk_score: 64,
      status: 'confirmed',
      assigned_to: '王 BP',
      escalated_to: null,
      message: '中风险：绩效下滑 + 邮件活跃度降低',
      created_at: new Date(Date.now() - 7200 * 1000).toISOString(),
      confirmed_at: null,
      closed_at: null,
    },
    {
      id: 'w-demo-3',
      employee_id: 'emp-118',
      prediction_id: 'pred-118',
      level: 'P2',
      risk_score: 48,
      status: 'fixing',
      assigned_to: '赵经理',
      escalated_to: null,
      message: '趋势：考勤异常增加',
      created_at: new Date(Date.now() - 86400 * 1000).toISOString(),
      confirmed_at: null,
      closed_at: null,
    },
  ]
}


function levelBadgeClass(level: string) {
  return `badge-${level.toLowerCase()}`
}

// ===== 实时风险推送（WS /ws/risk）：预测更新后防抖刷新 KPI/分布/最近预警 =====
const WS_RELOAD_DEBOUNCE_MS = 2000
let wsReloadTimer: ReturnType<typeof setTimeout> | null = null

function reloadOnRiskUpdate(_payload: RiskUpdatePayload): void {
  if (wsReloadTimer) clearTimeout(wsReloadTimer)
  wsReloadTimer = setTimeout(() => {
    wsReloadTimer = null
    void loadDashboard()
  }, WS_RELOAD_DEBOUNCE_MS)
}

// 已登录才建连（useWebSocket 内部自带重连退避与卸载清理；连接失败静默降级）
if (getAccessToken()) {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const wsUrl = `${scheme}://${window.location.host}/api/v1/ws/risk?token=${encodeURIComponent(getAccessToken() as string)}`
  const feed = useWebSocket(wsUrl)
  feed.on<RiskUpdatePayload>('risk_update', reloadOnRiskUpdate)
  feed.connect()
}

onUnmounted(() => {
  if (wsReloadTimer) clearTimeout(wsReloadTimer)
})

onMounted(loadDashboard)
</script>

<template>
  <div class="page">
    <h2 class="page-title">
      仪表盘
    </h2>
    <p class="page-desc">
      HR 经理视角的离职风险概览（KPI / 风险分布 / 最近预警 / 治理状态）
    </p>

    <div
      v-if="killSwitch.active"
      class="banner danger"
    >
      <span>🚫 Kill Switch 已激活：{{ killSwitch.reason || '未提供原因' }}</span>
      <span
        v-if="killSwitch.activated_at"
        style="margin-left:auto; font-size:12px;"
      >
        激活时间：{{ fmtTime(killSwitch.activated_at) }} | 操作人：{{ killSwitch.activated_by || '-' }}
      </span>
    </div>
    <div
      v-if="demoMode"
      class="banner warning"
    >
      <span>⚠ {{ errorMsg }}</span>
    </div>

    <!-- KPI 卡片行 -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">
          在职员工
        </div>
        <div class="kpi-value">
          {{ stats.totalEmployees }}
        </div>
        <div class="kpi-foot">
          总数
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">
          高风险人数
        </div>
        <div class="kpi-value risk-high">
          {{ stats.highRiskCount }}
        </div>
        <div class="kpi-foot">
          含中高 + 高
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">
          待处理预警
        </div>
        <div class="kpi-value risk-medium">
          {{ stats.pendingWarnings }}
        </div>
        <div class="kpi-foot">
          status=new
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">
          模型版本
        </div>
        <div class="kpi-value model-version">
          {{ stats.modelVersion }}
        </div>
        <div class="kpi-foot">
          融合模型
        </div>
      </div>
    </div>

    <div class="grid-2">
      <!-- 风险等级分布 -->
      <div class="card">
        <h3 class="card-title">
          风险等级分布
        </h3>
        <div class="dist-chart">
          <div
            v-for="d in distribution"
            :key="d.level"
            class="dist-row"
          >
            <div class="dist-label">
              <span
                class="dot"
                :style="{ background: levelColors[d.level] }"
              />
              {{ levelLabels[d.level] }}
            </div>
            <div class="dist-bar-bg">
              <div
                class="dist-bar"
                :style="{ width: (d.count / distMax) * 100 + '%', background: levelColors[d.level] }"
              />
            </div>
            <div class="dist-count">
              {{ d.count }}
            </div>
          </div>
        </div>
      </div>

      <!-- 最近 5 条预警 -->
      <div class="card">
        <h3 class="card-title">
          最近预警
        </h3>
        <div
          v-if="recentWarnings.length === 0"
          class="empty"
        >
          暂无预警数据
        </div>
        <ul
          v-else
          class="warning-list"
        >
          <li
            v-for="w in recentWarnings"
            :key="w.id"
            class="warning-item"
          >
            <span
              :class="levelBadgeClass(w.level)"
              style="margin-right:8px;"
            >{{ w.level }}</span>
            <div class="warning-body">
              <div class="warning-msg">
                {{ w.message || '无说明' }}
              </div>
              <div class="warning-meta">
                员工 {{ w.employee_id }} · 分 {{ w.risk_score }}
                · <span :class="'status-chip ' + w.status">{{ w.status }}</span>
                · {{ fmtTime(w.created_at) }}
              </div>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}
.kpi-card {
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 20px;
}
.kpi-label {
  font-size: 13px;
  color: var(--color-text-muted);
  margin-bottom: 10px;
}
.kpi-value {
  font-size: 32px;
  font-weight: 600;
  line-height: 1.1;
}
.kpi-value.model-version {
  font-size: 22px;
}
.kpi-foot {
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-text-muted);
}

.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 16px;
}

/* 风险分布条形图 */
.dist-chart {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.dist-row {
  display: grid;
  grid-template-columns: 90px 1fr 40px;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.dist-label {
  display: flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}
.dist-bar-bg {
  background: #f1f5f9;
  border-radius: 4px;
  height: 18px;
  overflow: hidden;
}
.dist-bar {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s;
  min-width: 2px;
}
.dist-count {
  text-align: right;
  font-weight: 600;
}

/* 预警列表 */
.warning-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.warning-item {
  display: flex;
  align-items: flex-start;
  padding: 10px 0;
  border-bottom: 1px solid var(--color-border);
  font-size: 13px;
}
.warning-item:last-child {
  border-bottom: none;
}
.warning-body {
  flex: 1;
  min-width: 0;
}
.warning-msg {
  font-weight: 500;
  margin-bottom: 4px;
  word-break: break-all;
}
.warning-meta {
  color: var(--color-text-muted);
  font-size: 12px;
}

@media (max-width: 900px) {
  .kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .grid-2 { grid-template-columns: 1fr; }
}
@media (max-width: 480px) {
  .kpi-grid { grid-template-columns: 1fr; }
  .dist-row { grid-template-columns: 80px 1fr 36px; }
}
</style>
