<script setup lang="ts">
// 预警中心视图 - 列表 + 筛选 + 分页 + 详情面板 + 状态机转换 + 申诉 + 标记
import { ref, computed, onMounted } from 'vue'
import { apiClient } from '@/api/client'
import type {
  WarningOut,
  WarningStatusUpdate,
  Paginated,
} from '@/api/types'

type Status = WarningOut['status']
type Level = WarningOut['level']

const items = ref<WarningOut[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)

const statusFilter = ref<Status | ''>('')
const levelFilter = ref<Level | ''>('')

const expandedId = ref<string | null>(null)
const expandedItem = ref<WarningOut | null>(null)

// 操作弹框状态
const actionModal = ref<{
  open: boolean
  kind: 'transition' | 'appeal' | 'mark' | ''
  targetStatus?: Status
  markType?: 'false_positive' | 'watching' | 'communicated'
  comment: string
  reason: string
  operatorId: string
  warningId: string
}>({
  open: false,
  kind: '',
  comment: '',
  reason: '',
  operatorId: '',
  warningId: '',
})

const errorMsg = ref('')
const actionMsg = ref('')
const actionLoading = ref(false)

const statusLabels: Record<Status, string> = {
  new: '新建',
  confirmed: '已确认',
  review: '复核中',
  fixing: '干预中',
  appealing: '申诉中',
  closed: '已关闭',
}

const markTypeLabels: Record<string, string> = {
  false_positive: '误报',
  watching: '关注',
  communicated: '已沟通',
}

// 状态机：根据当前状态返回可转换的状态（对齐后端 WarningService.allowed_next_statuses）
const statusTransitions: Record<Status, Array<{ status: Status; label: string }>> = {
  new: [
    { status: 'confirmed', label: '确认' },
    { status: 'closed', label: '关闭' },
  ],
  confirmed: [
    { status: 'review', label: '复核（P0）' },
    { status: 'fixing', label: '干预（P1/P2）' },
    { status: 'appealing', label: '申诉' },
    { status: 'closed', label: '关闭' },
  ],
  review: [
    { status: 'fixing', label: '干预' },
    { status: 'closed', label: '关闭' },
  ],
  fixing: [
    { status: 'review', label: '复核' },
    { status: 'closed', label: '关闭' },
  ],
  appealing: [
    { status: 'confirmed', label: '确认' },
    { status: 'closed', label: '关闭' },
  ],
  closed: [],
}

const availableTransitions = computed(() => {
  if (!expandedItem.value) return []
  return statusTransitions[expandedItem.value.status] || []
})

async function fetchWarnings() {
  loading.value = true
  errorMsg.value = ''
  try {
    const params: Record<string, unknown> = {
      page: page.value,
      page_size: pageSize.value,
    }
    if (statusFilter.value) params.status = statusFilter.value
    if (levelFilter.value) params.level = levelFilter.value
    const { data } = await apiClient.get<Paginated<WarningOut>>('/api/v1/warnings', { params })
    items.value = data.items
    total.value = data.total
  } catch (e: unknown) {
    const err = e as { message?: string }
    errorMsg.value = err?.message || '预警列表加载失败，已切换演示模式'
    fillDemo()
  } finally {
    loading.value = false
  }
}

function fillDemo() {
  const now = Date.now()
  items.value = [
    { id: 'w-001', employee_id: 'emp-001', prediction_id: 'pred-001', level: 'P0', risk_score: 87, status: 'new', assigned_to: '李 HR', escalated_to: null, message: '高风险：连续 5 天未登录 + 加班时长激增', created_at: new Date(now - 3600 * 1000).toISOString(), confirmed_at: null, closed_at: null },
    { id: 'w-002', employee_id: 'emp-042', prediction_id: 'pred-042', level: 'P1', risk_score: 64, status: 'confirmed', assigned_to: '王 BP', escalated_to: null, message: '中风险：绩效下滑 + 邮件活跃度降低', created_at: new Date(now - 7200 * 1000).toISOString(), confirmed_at: null, closed_at: null },
    { id: 'w-003', employee_id: 'emp-118', prediction_id: 'pred-118', level: 'P2', risk_score: 48, status: 'fixing', assigned_to: '赵经理', escalated_to: null, message: '趋势：考勤异常增加', created_at: new Date(now - 86400 * 1000).toISOString(), confirmed_at: null, closed_at: null },
    { id: 'w-004', employee_id: 'emp-203', prediction_id: 'pred-203', level: 'P1', risk_score: 71, status: 'appealing', assigned_to: '钱主管', escalated_to: null, message: '员工对预测结果申诉中', created_at: new Date(now - 2 * 86400 * 1000).toISOString(), confirmed_at: null, closed_at: null },
    { id: 'w-005', employee_id: 'emp-317', prediction_id: 'pred-317', level: 'P2', risk_score: 38, status: 'closed', assigned_to: '孙总监', escalated_to: null, message: '已沟通确认，离职风险已缓解', created_at: new Date(now - 3 * 86400 * 1000).toISOString(), confirmed_at: null, closed_at: null },
  ]
  total.value = items.value.length
}

function levelBadgeClass(level: string) {
  return `badge-${level.toLowerCase()}`
}

async function toggleDetail(w: WarningOut) {
  if (expandedId.value === w.id) {
    expandedId.value = null
    expandedItem.value = null
    return
  }
  expandedId.value = w.id
  // 拉取详情
  try {
    const { data } = await apiClient.get<WarningOut>(`/api/v1/warnings/${w.id}`)
    expandedItem.value = data
  } catch {
    // 接口失败就用列表项
    expandedItem.value = w
  }
}

function openTransition(target: Status) {
  if (!expandedItem.value) return
  actionModal.value = {
    open: true,
    kind: 'transition',
    targetStatus: target,
    comment: '',
    reason: '',
    operatorId: '',
    warningId: expandedItem.value.id,
  }
}

function openAppeal() {
  if (!expandedItem.value) return
  actionModal.value = {
    open: true,
    kind: 'appeal',
    reason: '',
    comment: '',
    operatorId: '',
    warningId: expandedItem.value.id,
  }
}

function openMark(mt: 'false_positive' | 'watching' | 'communicated') {
  if (!expandedItem.value) return
  actionModal.value = {
    open: true,
    kind: 'mark',
    markType: mt,
    comment: '',
    reason: '',
    operatorId: '',
    warningId: expandedItem.value.id,
  }
}

function closeModal() {
  actionModal.value.open = false
  actionModal.value.kind = ''
  actionModal.value.comment = ''
  actionModal.value.reason = ''
  actionModal.value.operatorId = ''
  actionModal.value.targetStatus = undefined
  actionModal.value.markType = undefined
  actionMsg.value = ''
}

async function submitAction() {
  if (!actionModal.value.warningId) return
  if (!actionModal.value.operatorId) {
    actionMsg.value = '请填写操作人 ID'
    return
  }
  actionLoading.value = true
  actionMsg.value = ''
  try {
    const wid = actionModal.value.warningId
    const opId = actionModal.value.operatorId
    if (actionModal.value.kind === 'transition' && actionModal.value.targetStatus) {
      const body: WarningStatusUpdate = {
        target_status: actionModal.value.targetStatus,
        operator_id: opId,
        comment: actionModal.value.comment || undefined,
      }
      const { data } = await apiClient.patch<WarningOut>(`/api/v1/warnings/${wid}/status`, body)
      expandedItem.value = data
      // 同步列表
      const idx = items.value.findIndex((x) => x.id === wid)
      if (idx >= 0) items.value[idx] = data
    } else if (actionModal.value.kind === 'appeal') {
      if (!actionModal.value.reason) {
        actionMsg.value = '请填写申诉理由'
        actionLoading.value = false
        return
      }
      const { data } = await apiClient.post<WarningOut>(`/api/v1/warnings/${wid}/appeal`, {
        reason: actionModal.value.reason,
        operator_id: opId,
      })
      expandedItem.value = data
      const idx = items.value.findIndex((x) => x.id === wid)
      if (idx >= 0) items.value[idx] = data
    } else if (actionModal.value.kind === 'mark' && actionModal.value.markType) {
      const { data } = await apiClient.post<WarningOut>(`/api/v1/warnings/${wid}/mark`, {
        mark_type: actionModal.value.markType,
        comment: actionModal.value.comment,
        operator_id: opId,
      })
      expandedItem.value = data
      const idx = items.value.findIndex((x) => x.id === wid)
      if (idx >= 0) items.value[idx] = data
    }
    closeModal()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    actionMsg.value = err?.response?.data?.detail || '操作失败'
  } finally {
    actionLoading.value = false
  }
}

function prevPage() {
  if (page.value > 1) {
    page.value--
    fetchWarnings()
  }
}
function nextPage() {
  if (page.value * pageSize.value < total.value) {
    page.value++
    fetchWarnings()
  }
}

function fmtTime(s: string | null) {
  if (!s) return '-'
  try {
    return new Date(s).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return s
  }
}

function onFilterChange() {
  page.value = 1
  fetchWarnings()
}

onMounted(fetchWarnings)
</script>

<template>
  <div class="page">
    <h2 class="page-title">预警中心</h2>
    <p class="page-desc">预警状态机：P0 confirmed→review→fixing；P1/P2 confirmed→fixing 直转（D04 4.3）</p>

    <div v-if="errorMsg" class="banner warning">⚠ {{ errorMsg }}</div>

    <!-- 筛选 -->
    <div class="toolbar">
      <select v-model="statusFilter" @change="onFilterChange">
        <option value="">全部状态</option>
        <option value="new">新建</option>
        <option value="confirmed">已确认</option>
        <option value="review">复核中</option>
        <option value="fixing">干预中</option>
        <option value="appealing">申诉中</option>
        <option value="closed">已关闭</option>
      </select>
      <select v-model="levelFilter" @change="onFilterChange">
        <option value="">全部等级</option>
        <option value="P0">P0 高级</option>
        <option value="P1">P1 中级</option>
        <option value="P2">P2 趋势</option>
      </select>
      <button class="secondary" @click="fetchWarnings" :disabled="loading">
        {{ loading ? '加载中...' : '刷新' }}
      </button>
    </div>

    <div class="card">
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>等级</th>
              <th>员工</th>
              <th>风险分</th>
              <th>状态</th>
              <th>分配</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="w in items" :key="w.id">
              <tr class="clickable" @click="toggleDetail(w)">
                <td><span :class="levelBadgeClass(w.level)">{{ w.level }}</span></td>
                <td>{{ w.employee_id }}</td>
                <td :class="'risk-' + (w.risk_score >= 70 ? 'high' : w.risk_score >= 40 ? 'medium' : 'low')">
                  {{ w.risk_score }}
                </td>
                <td><span :class="'status-chip ' + w.status">{{ statusLabels[w.status] }}</span></td>
                <td>{{ w.assigned_to || '-' }}</td>
                <td>{{ fmtTime(w.created_at) }}</td>
                <td>
                  <button class="secondary small-btn" @click.stop="toggleDetail(w)">
                    {{ expandedId === w.id ? '收起' : '详情' }}
                  </button>
                </td>
              </tr>
              <tr v-if="expandedId === w.id" class="detail-row">
                <td colspan="7">
                  <div class="detail-panel">
                    <div v-if="!expandedItem" class="empty">加载中...</div>
                    <div v-else>
                      <div class="detail-grid">
                        <div><span class="d-label">预警 ID：</span>{{ expandedItem.id }}</div>
                        <div><span class="d-label">员工 ID：</span>{{ expandedItem.employee_id }}</div>
                        <div><span class="d-label">预测 ID：</span>{{ expandedItem.prediction_id || '-' }}</div>
                        <div><span class="d-label">等级：</span><span :class="levelBadgeClass(expandedItem.level)">{{ expandedItem.level }}</span></div>
                        <div><span class="d-label">风险分：</span>{{ expandedItem.risk_score }}</div>
                        <div><span class="d-label">状态：</span><span :class="'status-chip ' + expandedItem.status">{{ statusLabels[expandedItem.status] }}</span></div>
                        <div><span class="d-label">分配给：</span>{{ expandedItem.assigned_to || '-' }}</div>
                        <div><span class="d-label">升级给：</span>{{ expandedItem.escalated_to || '-' }}</div>
                        <div><span class="d-label">创建时间：</span>{{ fmtTime(expandedItem.created_at) }}</div>
                        <div><span class="d-label">确认时间：</span>{{ fmtTime(expandedItem.confirmed_at) }}</div>
                        <div><span class="d-label">关闭时间：</span>{{ fmtTime(expandedItem.closed_at) }}</div>
                      </div>
                      <div v-if="expandedItem.message" class="detail-msg">
                        <span class="d-label">说明：</span>{{ expandedItem.message }}
                      </div>

                      <!-- 状态机转换按钮 -->
                      <div class="action-section">
                        <div class="action-title">状态转换</div>
                        <div class="action-btns">
                          <button
                            v-for="t in availableTransitions"
                            :key="t.status"
                            class="small-btn"
                            @click="openTransition(t.status)"
                          >
                            {{ t.label }}
                          </button>
                          <span v-if="availableTransitions.length === 0" class="muted">无可转换状态（终态）</span>
                        </div>
                      </div>

                      <!-- 申诉按钮 -->
                      <div class="action-section" v-if="expandedItem.status !== 'closed' && expandedItem.status !== 'appealing'">
                        <div class="action-title">申诉</div>
                        <div class="action-btns">
                          <button class="small-btn secondary" @click="openAppeal">发起申诉</button>
                        </div>
                      </div>

                      <!-- 标记按钮 -->
                      <div class="action-section" v-if="expandedItem.status !== 'closed'">
                        <div class="action-title">标记</div>
                        <div class="action-btns">
                          <button class="small-btn secondary" @click="openMark('false_positive')">误报</button>
                          <button class="small-btn secondary" @click="openMark('watching')">关注</button>
                          <button class="small-btn secondary" @click="openMark('communicated')">已沟通</button>
                        </div>
                      </div>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="items.length === 0">
              <td colspan="7" class="empty">暂无预警</td>
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

    <!-- 操作弹框 -->
    <div v-if="actionModal.open" class="modal-mask" @click.self="closeModal">
      <div class="modal">
        <div class="modal-title">
          <template v-if="actionModal.kind === 'transition'">状态转换：→ {{ actionModal.targetStatus && statusLabels[actionModal.targetStatus] }}</template>
          <template v-else-if="actionModal.kind === 'appeal'">发起申诉</template>
          <template v-else-if="actionModal.kind === 'mark' && actionModal.markType">标记：{{ markTypeLabels[actionModal.markType] }}</template>
        </div>
        <div class="modal-body">
          <div class="form-item">
            <label>操作人 ID <span class="required">*</span></label>
            <input v-model="actionModal.operatorId" placeholder="如 admin@hra.demo" />
          </div>
          <div v-if="actionModal.kind === 'appeal'" class="form-item">
            <label>申诉理由 <span class="required">*</span></label>
            <textarea v-model="actionModal.reason" rows="3" placeholder="请输入申诉理由"></textarea>
          </div>
          <div v-else class="form-item">
            <label>{{ actionModal.kind === 'mark' ? '标记说明' : '备注' }}</label>
            <textarea v-model="actionModal.comment" rows="3" :placeholder="actionModal.kind === 'mark' ? '请输入标记说明' : '请输入备注（可选）'"></textarea>
          </div>
          <p v-if="actionMsg" class="action-error">{{ actionMsg }}</p>
        </div>
        <div class="modal-foot">
          <button class="secondary" @click="closeModal" :disabled="actionLoading">取消</button>
          <button @click="submitAction" :disabled="actionLoading">
            {{ actionLoading ? '提交中...' : '提交' }}
          </button>
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
  align-items: center;
}
.toolbar select {
  min-width: 140px;
}
.card {
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 16px;
}

/* 详情面板 */
.detail-row > td {
  background: #f8fafc;
  padding: 0 !important;
}
.detail-panel {
  padding: 16px 20px;
  border-top: 2px solid var(--color-primary);
}
.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
  font-size: 13px;
  margin-bottom: 12px;
}
.d-label {
  color: var(--color-text-muted);
  margin-right: 4px;
}
.detail-msg {
  font-size: 13px;
  margin-bottom: 12px;
  padding: 8px 10px;
  background: white;
  border-radius: 4px;
  border: 1px solid var(--color-border);
}
.action-section {
  margin-top: 12px;
}
.action-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--color-text-muted);
}
.action-btns {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.small-btn {
  padding: 4px 10px;
  font-size: 12px;
}
.muted {
  color: var(--color-text-muted);
  font-size: 13px;
}

/* 分页 */
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

/* 弹框 */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 16px;
}
.modal {
  background: white;
  border-radius: 8px;
  width: 100%;
  max-width: 480px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.2);
}
.modal-title {
  font-size: 15px;
  font-weight: 600;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border);
}
.modal-body {
  padding: 16px 20px;
}
.modal-foot {
  padding: 12px 20px;
  border-top: 1px solid var(--color-border);
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.form-item {
  margin-bottom: 12px;
}
.form-item label {
  display: block;
  font-size: 13px;
  color: var(--color-text-muted);
  margin-bottom: 6px;
}
.required {
  color: var(--color-danger);
}
.form-item input,
.form-item textarea {
  width: 100%;
}
.form-item textarea {
  resize: vertical;
  min-height: 60px;
}
.action-error {
  color: var(--color-danger);
  font-size: 13px;
  background: #fef2f2;
  padding: 6px 10px;
  border-radius: 4px;
  border: 1px solid #fecaca;
}
</style>
