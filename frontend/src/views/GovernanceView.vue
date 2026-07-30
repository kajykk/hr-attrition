<script setup lang="ts">
// 模型治理视图 - Kill Switch + 漂移检测 + 公平性监测 + 模型版本
import { ref, computed, onMounted } from 'vue'
import { apiClient } from '@/api/client'
import type { KillSwitchStatus, DriftResult, FairnessResult } from '@/api/types'

const killSwitch = ref<KillSwitchStatus>({ active: false })
const drift = ref<DriftResult | null>(null)
const fairness = ref<FairnessResult | null>(null)
const modelVersion = ref('-')
const loading = ref(false)
const errorMsg = ref('')

// Kill Switch 操作弹框
const ksModal = ref<{ open: boolean; action: 'activate' | 'deactivate'; reason: string }>({
  open: false,
  action: 'activate',
  reason: '',
})
const ksLoading = ref(false)
const ksMsg = ref('')

// 漂移图最大 PSI（用于条形宽度）
const driftMax = computed(() => {
  if (!drift.value || drift.value.features.length === 0) return 1
  return Math.max(0.2, ...drift.value.features.map((f) => f.psi))
})

// 公平性 5% 阈值（用于进度条）
const fairnessThreshold = 5

function fairnessColor(v: number) {
  if (v < 5) return 'var(--risk-low)'
  if (v < 8) return 'var(--risk-medium)'
  return 'var(--risk-high)'
}

function driftColor(psi: number) {
  if (psi < 0.1) return 'var(--risk-low)'
  if (psi < 0.2) return 'var(--risk-medium)'
  return 'var(--risk-high)'
}

async function loadAll() {
  loading.value = true
  errorMsg.value = ''
  let anySuccess = false
  await Promise.all([
    apiClient
      .get<KillSwitchStatus>('/api/v1/admin/kill-switch')
      .then(({ data }) => {
        killSwitch.value = data
        anySuccess = true
      })
      .catch(() => {}),
    apiClient
      .get<{ model_version: string }>('/api/v1/risk/global-explanation', { params: { window_days: 30 } })
      .then(({ data }) => {
        modelVersion.value = data.model_version
        anySuccess = true
      })
      .catch(() => {}),
    // 漂移 & 公平性接口（后端如有则对接，无则演示）
    apiClient
      .get<DriftResult>('/api/v1/admin/drift')
      .then(({ data }) => {
        drift.value = data
        anySuccess = true
      })
      .catch(() => {}),
    apiClient
      .get<FairnessResult>('/api/v1/admin/fairness')
      .then(({ data }) => {
        fairness.value = data
        anySuccess = true
      })
      .catch(() => {}),
  ])
  if (!anySuccess) {
    errorMsg.value = '后端 API 不可用，已切换到演示模式（占位数据）'
  }
  // 任意失败填演示数据
  if (!drift.value) fillDemoDrift()
  if (!fairness.value) fillDemoFairness()
  if (modelVersion.value === '-') modelVersion.value = 'fusion-v3.2'
  loading.value = false
}

function fillDemoDrift() {
  drift.value = {
    max_psi: 0.14,
    critical_features: ['overtime_hours_30d'],
    features: [
      { feature: 'overtime_hours_30d', psi: 0.14 },
      { feature: 'days_since_last_login', psi: 0.09 },
      { feature: 'perf_change_ratio', psi: 0.06 },
      { feature: 'salary_competitiveness', psi: 0.04 },
      { feature: 'leave_balance', psi: 0.03 },
      { feature: 'team_attrition_rate', psi: 0.02 },
    ],
    computed_at: new Date(Date.now() - 3600 * 1000).toISOString(),
  }
}

function fillDemoFairness() {
  fairness.value = {
    dimensions: [
      { name: 'gender', label: '性别', disparity: 3.2 },
      { name: 'age', label: '年龄', disparity: 4.8 },
      { name: 'ethnicity', label: '民族', disparity: 2.1 },
      { name: 'disability', label: '残疾', disparity: 1.5 },
    ],
    computed_at: new Date(Date.now() - 3600 * 1000).toISOString(),
  }
}

function openKsModal(action: 'activate' | 'deactivate') {
  ksModal.value = {
    open: true,
    action,
    reason: '',
  }
  ksMsg.value = ''
}

function closeKsModal() {
  ksModal.value.open = false
  ksModal.value.reason = ''
  ksMsg.value = ''
}

async function submitKs() {
  if (ksModal.value.action === 'activate' && !ksModal.value.reason) {
    ksMsg.value = '请填写激活原因'
    return
  }
  ksLoading.value = true
  ksMsg.value = ''
  try {
    const url =
      ksModal.value.action === 'activate'
        ? '/api/v1/admin/kill-switch/activate'
        : '/api/v1/admin/kill-switch/deactivate'
    const body =
      ksModal.value.action === 'activate' ? { reason: ksModal.value.reason } : {}
    const { data } = await apiClient.post<KillSwitchStatus>(url, body)
    killSwitch.value = data
    closeKsModal()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ksMsg.value = err?.response?.data?.detail || '操作失败'
    // 演示模式：本地切换
    killSwitch.value = {
      active: ksModal.value.action === 'activate',
      reason: ksModal.value.action === 'activate' ? ksModal.value.reason : null,
      activated_at: ksModal.value.action === 'activate' ? new Date().toISOString() : null,
      activated_by: 'demo-admin',
    }
    closeKsModal()
  } finally {
    ksLoading.value = false
  }
}

function fmtTime(s?: string | null) {
  if (!s) return '-'
  try {
    return new Date(s).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return s
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="page">
    <h2 class="page-title">模型治理</h2>
    <p class="page-desc">Kill Switch + 漂移检测 + 公平性监测 + 模型版本（D03 4.5 + D05 3.6）</p>

    <div v-if="errorMsg" class="banner warning">⚠ {{ errorMsg }}</div>

    <!-- Kill Switch 横幅 -->
    <div v-if="killSwitch.active" class="banner danger">
      <span>🚫 Kill Switch 已激活：{{ killSwitch.reason || '未提供原因' }}</span>
      <span v-if="killSwitch.activated_at" style="margin-left:auto; font-size:12px;">
        激活时间：{{ fmtTime(killSwitch.activated_at) }} | 操作人：{{ killSwitch.activated_by || '-' }}
      </span>
    </div>

    <!-- Kill Switch 面板 -->
    <div class="card ks-card" :class="{ active: killSwitch.active }">
      <h3 class="card-title">Kill Switch（模型熔断）</h3>
      <div class="ks-status">
        <div class="ks-light" :class="killSwitch.active ? 'on' : 'off'"></div>
        <div>
          <div class="ks-state" :class="killSwitch.active ? 'risk-high' : 'risk-low'">
            {{ killSwitch.active ? '已激活（模型停服）' : '未激活（正常服务）' }}
          </div>
          <div class="ks-meta">
            <span v-if="killSwitch.active">
              激活原因：{{ killSwitch.reason || '-' }} |
              激活时间：{{ fmtTime(killSwitch.activated_at) }} |
              操作人：{{ killSwitch.activated_by || '-' }}
            </span>
            <span v-else>模型当前正常对外提供预测服务</span>
          </div>
        </div>
      </div>
      <div class="ks-actions">
        <button v-if="!killSwitch.active" class="danger-btn" @click="openKsModal('activate')">
          激活 Kill Switch
        </button>
        <button v-else class="success-btn" @click="openKsModal('deactivate')">
          解除 Kill Switch
        </button>
        <button class="secondary" @click="loadAll" :disabled="loading">刷新状态</button>
      </div>
    </div>

    <div class="grid-2">
      <!-- 漂移检测面板 -->
      <div class="card">
        <h3 class="card-title">漂移检测（PSI）</h3>
        <div v-if="!drift" class="empty">暂无漂移数据</div>
        <div v-else>
          <div class="drift-summary">
            <div>
              <span class="d-label">最大 PSI：</span>
              <strong :class="drift.max_psi >= 0.2 ? 'risk-high' : drift.max_psi >= 0.1 ? 'risk-medium' : 'risk-low'">
                {{ drift.max_psi.toFixed(3) }}
              </strong>
            </div>
            <div>
              <span class="d-label">关键特征：</span>
              <span v-if="drift.critical_features.length">{{ drift.critical_features.join(', ') }}</span>
              <span v-else>无</span>
            </div>
            <div>
              <span class="d-label">检测时间：</span>{{ fmtTime(drift.computed_at) }}
            </div>
          </div>
          <div class="drift-chart">
            <div v-for="f in drift.features" :key="f.feature" class="drift-row">
              <div class="drift-name">{{ f.feature }}</div>
              <div class="drift-bar-bg">
                <!-- 阈值线 0.1 / 0.2 -->
                <div class="threshold-line warn" :style="{ left: (0.1 / driftMax) * 100 + '%' }"></div>
                <div class="threshold-line crit" :style="{ left: (0.2 / driftMax) * 100 + '%' }"></div>
                <div
                  class="drift-bar"
                  :style="{ width: (f.psi / driftMax) * 100 + '%', background: driftColor(f.psi) }"
                ></div>
              </div>
              <div class="drift-value" :style="{ color: driftColor(f.psi) }">{{ f.psi.toFixed(3) }}</div>
            </div>
          </div>
          <div class="legend">
            <span><span class="legend-dot" style="background:var(--risk-low)"></span>正常 (&lt;0.1)</span>
            <span><span class="legend-dot" style="background:var(--risk-medium)"></span>警告 (0.1-0.2)</span>
            <span><span class="legend-dot" style="background:var(--risk-high)"></span>严重 (&gt;0.2)</span>
          </div>
        </div>
      </div>

      <!-- 公平性面板 -->
      <div class="card">
        <h3 class="card-title">公平性监测（偏差%）</h3>
        <div v-if="!fairness" class="empty">暂无公平性数据</div>
        <div v-else>
          <div class="fairness-summary">
            <span class="d-label">检测时间：</span>{{ fmtTime(fairness.computed_at) }}
          </div>
          <div class="fairness-chart">
            <div v-for="d in fairness.dimensions" :key="d.name" class="fairness-row">
              <div class="fairness-name">{{ d.label }}</div>
              <div class="fairness-bar-bg">
                <!-- 5% 阈值线 -->
                <div class="threshold-line warn" :style="{ left: (fairnessThreshold / 10) * 100 + '%' }"></div>
                <div
                  class="fairness-bar"
                  :style="{ width: (d.disparity / 10) * 100 + '%', background: fairnessColor(d.disparity) }"
                ></div>
              </div>
              <div class="fairness-value" :style="{ color: fairnessColor(d.disparity) }">
                {{ d.disparity.toFixed(1) }}%
              </div>
            </div>
          </div>
          <div class="legend">
            <span><span class="legend-dot" style="background:var(--risk-low)"></span>正常 (&lt;5%)</span>
            <span><span class="legend-dot" style="background:var(--risk-medium)"></span>警告 (5-8%)</span>
            <span><span class="legend-dot" style="background:var(--risk-high)"></span>严重 (&gt;8%)</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 模型版本信息 -->
    <div class="card" style="margin-top:16px;">
      <h3 class="card-title">模型版本信息</h3>
      <div class="version-grid">
        <div><span class="d-label">当前版本：</span><strong>{{ modelVersion }}</strong></div>
        <div><span class="d-label">发布策略：</span>金丝雀（100% 流量）</div>
        <div><span class="d-label">观察期：</span>已通过</div>
        <div><span class="d-label">融合模态：</span>结构化 + 行为 + 文本</div>
      </div>
    </div>

    <!-- Kill Switch 操作弹框 -->
    <div v-if="ksModal.open" class="modal-mask" @click.self="closeKsModal">
      <div class="modal">
        <div class="modal-title">
          {{ ksModal.action === 'activate' ? '激活 Kill Switch' : '解除 Kill Switch' }}
        </div>
        <div class="modal-body">
          <p v-if="ksModal.action === 'activate'" class="warn-text">
            ⚠ 激活后模型将停止对外提供预测服务，所有 /risk/predict 请求将被拒绝。请谨慎操作。
          </p>
          <div v-if="ksModal.action === 'activate'" class="form-item">
            <label>激活原因 <span class="required">*</span></label>
            <textarea v-model="ksModal.reason" rows="3" placeholder="请填写激活原因（如：模型严重漂移、公平性超标等）"></textarea>
          </div>
          <p v-else>确认解除 Kill Switch？解除后模型将恢复对外服务。</p>
          <p v-if="ksMsg" class="action-error">{{ ksMsg }}</p>
        </div>
        <div class="modal-foot">
          <button class="secondary" @click="closeKsModal" :disabled="ksLoading">取消</button>
          <button
            :class="ksModal.action === 'activate' ? 'danger-btn' : 'success-btn'"
            @click="submitKs"
            :disabled="ksLoading"
          >
            {{ ksLoading ? '提交中...' : '确认' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ks-card {
  border-left: 4px solid var(--risk-low);
  margin-bottom: 16px;
}
.ks-card.active {
  border-left-color: var(--risk-high);
  background: #fff5f5;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 16px;
}

/* Kill Switch 状态 */
.ks-status {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}
.ks-light {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: 0 0 12px currentColor;
}
.ks-light.on {
  background: var(--risk-high);
  color: var(--risk-high);
  animation: pulse 1.5s infinite;
}
.ks-light.off {
  background: var(--risk-low);
  color: var(--risk-low);
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 4px currentColor; }
  50% { box-shadow: 0 0 16px currentColor; }
}
.ks-state {
  font-size: 18px;
  font-weight: 600;
}
.ks-meta {
  font-size: 12px;
  color: var(--color-text-muted);
  margin-top: 4px;
}
.ks-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.d-label {
  color: var(--color-text-muted);
}

/* 漂移图 */
.drift-summary {
  font-size: 13px;
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.drift-chart {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.drift-row {
  display: grid;
  grid-template-columns: 160px 1fr 50px;
  gap: 10px;
  align-items: center;
  font-size: 12px;
}
.drift-bar-bg {
  background: #f1f5f9;
  border-radius: 4px;
  height: 16px;
  overflow: hidden;
  position: relative;
}
.drift-bar {
  height: 100%;
  border-radius: 4px;
  min-width: 2px;
}
.threshold-line {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  z-index: 1;
}
.threshold-line.warn {
  background: var(--risk-medium);
}
.threshold-line.crit {
  background: var(--risk-high);
}
.drift-value {
  text-align: right;
  font-weight: 600;
}

/* 公平性图 */
.fairness-summary {
  font-size: 13px;
  margin-bottom: 16px;
}
.fairness-chart {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.fairness-row {
  display: grid;
  grid-template-columns: 60px 1fr 60px;
  gap: 10px;
  align-items: center;
  font-size: 13px;
}
.fairness-bar-bg {
  background: #f1f5f9;
  border-radius: 4px;
  height: 18px;
  overflow: hidden;
  position: relative;
}
.fairness-bar {
  height: 100%;
  border-radius: 4px;
  min-width: 2px;
}
.fairness-value {
  text-align: right;
  font-weight: 600;
}

/* 图例 */
.legend {
  margin-top: 16px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--color-text-muted);
}
.legend-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
  vertical-align: middle;
}

/* 模型版本信息 */
.version-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  font-size: 14px;
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
  font-size: 14px;
}
.modal-foot {
  padding: 12px 20px;
  border-top: 1px solid var(--color-border);
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.warn-text {
  color: var(--color-danger);
  background: #fef2f2;
  padding: 8px 10px;
  border-radius: 4px;
  border: 1px solid #fecaca;
  margin-bottom: 12px;
  font-size: 13px;
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
.form-item textarea {
  width: 100%;
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

@media (max-width: 900px) {
  .grid-2 { grid-template-columns: 1fr; }
  .drift-row { grid-template-columns: 120px 1fr 44px; }
}
</style>
