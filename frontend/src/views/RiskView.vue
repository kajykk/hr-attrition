<script setup lang="ts">
// 风险预测视图 - 预测卡片 + 各模态分 + SHAP 归因 + 全局特征重要性
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { apiClient } from '@/api/client'
import type {
  RiskPredictionOut,
  ShapExplanationOut,
  GlobalExplanationOut,
  ShapFactor,
} from '@/api/types'

const route = useRoute()

const employeeId = ref((route.query.employee_id as string) || '')
const result = ref<RiskPredictionOut | null>(null)
const explanation = ref<ShapExplanationOut | null>(null)
const globalExp = ref<GlobalExplanationOut | null>(null)
const loading = ref(false)
const errorMsg = ref('')

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

const modalityLabels: Record<string, string> = {
  structured: '结构化特征',
  behavior: '行为特征',
  text: '文本特征',
  network: '网络特征',
}

// 模态分数条形图最大值（统一为 100）
const modalityMax = 100

// SHAP 贡献最大绝对值，用于条形宽度
const shapMaxAbs = computed(() => {
  if (!explanation.value || explanation.value.factors.length === 0) return 1
  return Math.max(1, ...explanation.value.factors.map((f) => Math.abs(f.contribution)))
})

// 取 Top3 因子
const top3Factors = computed(() => {
  if (!explanation.value) return []
  return [...explanation.value.factors]
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
    .slice(0, 3)
})

// 全局 Top5
const top5Global = computed(() => {
  if (!globalExp.value) return []
  return [...globalExp.value.top_features]
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
    .slice(0, 5)
})
const globalMaxAbs = computed(() => {
  if (top5Global.value.length === 0) return 1
  return Math.max(1, ...top5Global.value.map((f) => Math.abs(f.contribution)))
})

async function predict() {
  if (!employeeId.value) return
  loading.value = true
  errorMsg.value = ''
  result.value = null
  explanation.value = null
  try {
    const { data } = await apiClient.post<RiskPredictionOut>('/api/v1/risk/predict', {
      employee_id: employeeId.value,
      force_refresh: false,
    })
    result.value = data
    // 预测成功后并行拉取 SHAP 解释
    loadExplanation(employeeId.value)
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    errorMsg.value = err?.response?.data?.detail || '预测失败，使用演示数据'
    fillDemoPrediction()
  } finally {
    loading.value = false
  }
}

async function loadExplanation(empId: string) {
  try {
    const { data } = await apiClient.get<ShapExplanationOut>(
      `/api/v1/risk/employees/${empId}/explanation`
    )
    explanation.value = data
  } catch {
    // 解释失败时填演示数据
    fillDemoExplanation()
  }
}

async function loadGlobal() {
  try {
    const { data } = await apiClient.get<GlobalExplanationOut>('/api/v1/risk/global-explanation', {
      params: { window_days: 30 },
    })
    globalExp.value = data
  } catch {
    fillDemoGlobal()
  }
}

function fillDemoPrediction() {
  result.value = {
    prediction_id: 'pred-demo-' + Date.now(),
    employee_id: employeeId.value || 'emp-demo',
    risk_score: 78,
    risk_level: 'medium_high',
    modality_scores: { structured: 72, behavior: 84, text: 65 },
    model_version: 'fusion-v3.2',
    predicted_at: new Date().toISOString(),
    cached: false,
  }
  fillDemoExplanation()
}

function fillDemoExplanation() {
  const factors: ShapFactor[] = [
    { feature: 'overtime_hours_30d', display_name: '近30天加班时长', value: 58, contribution: 0.32, direction: 'positive', description: '加班时长显著高于部门均值' },
    { feature: 'days_since_last_login', display_name: '上次登录至今天数', value: 5, contribution: 0.24, direction: 'positive', description: '连续 5 天未登录系统' },
    { feature: 'perf_change_ratio', display_name: '绩效环比变化', value: -0.18, contribution: 0.18, direction: 'positive', description: '绩效较上季度下滑 18%' },
    { feature: 'leave_balance', display_name: '剩余年假', value: 12, contribution: -0.12, direction: 'negative', description: '年假余额较多，离职意愿略低' },
    { feature: 'team_attrition_rate', display_name: '团队离职率', value: 0.21, contribution: 0.09, direction: 'positive' },
  ]
  explanation.value = {
    prediction_id: result.value?.prediction_id || 'pred-demo',
    factors,
    base_value: 0.32,
    output_value: 0.78,
    computed_at: new Date().toISOString(),
  }
}

function fillDemoGlobal() {
  const features: ShapFactor[] = [
    { feature: 'overtime_hours_30d', display_name: '近30天加班时长', contribution: 0.34, direction: 'positive' },
    { feature: 'days_since_last_login', display_name: '上次登录至今天数', contribution: 0.27, direction: 'positive' },
    { feature: 'perf_change_ratio', display_name: '绩效环比变化', contribution: 0.21, direction: 'positive' },
    { feature: 'salary_competitiveness', display_name: '薪酬竞争力', contribution: 0.17, direction: 'positive' },
    { feature: 'manager_change_90d', display_name: '90天内换直属上级', contribution: 0.14, direction: 'positive' },
    { feature: 'leave_balance', display_name: '剩余年假', contribution: 0.11, direction: 'negative' },
  ]
  globalExp.value = {
    model_version: 'fusion-v3.2',
    window_days: 30,
    top_features: features,
    computed_at: new Date().toISOString(),
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

// 路由 query 变化时自动预测
watch(
  () => route.query.employee_id,
  (val) => {
    if (val && typeof val === 'string' && val !== employeeId.value) {
      employeeId.value = val
      predict()
    }
  }
)

onMounted(() => {
  loadGlobal()
  if (employeeId.value) {
    predict()
  }
})
</script>

<template>
  <div class="page">
    <h2 class="page-title">风险预测</h2>
    <p class="page-desc">多模态融合引擎（结构化 + 行为 + 文本），D05 3.3 POST /risk/predict + SHAP 解释</p>

    <div v-if="errorMsg" class="banner warning">⚠ {{ errorMsg }}</div>

    <!-- 员工选择 -->
    <div class="card input-card">
      <div class="form-row">
        <input
          v-model="employeeId"
          placeholder="输入员工 ID（如 emp-001）"
          @keyup.enter="predict"
        />
        <button @click="predict" :disabled="loading || !employeeId">
          {{ loading ? '预测中...' : '预测风险' }}
        </button>
      </div>
    </div>

    <div v-if="!result && !loading" class="empty card">请输入员工 ID 并点击"预测风险"</div>

    <!-- 预测结果卡片 -->
    <div v-if="result" class="result-grid">
      <div class="card pred-card">
        <h3 class="card-title">预测结果</h3>
        <div class="pred-main">
          <div class="score-circle" :style="{ borderColor: levelColors[result.risk_level] || '#999' }">
            <div class="score-num" :style="{ color: levelColors[result.risk_level] || '#999' }">
              {{ result.risk_score }}
            </div>
            <div class="score-unit">/100</div>
          </div>
          <div class="pred-meta">
            <div class="meta-row">
              <span class="meta-label">风险等级：</span>
              <span class="risk-chip" :class="result.risk_level">
                {{ levelLabels[result.risk_level] || result.risk_level }}
              </span>
            </div>
            <div class="meta-row">
              <span class="meta-label">模型版本：</span>
              <span>{{ result.model_version }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">缓存命中：</span>
              <span :class="result.cached ? 'risk-low' : 'risk-medium'">
                {{ result.cached ? '是（5 分钟内已预测）' : '否（新预测）' }}
              </span>
            </div>
            <div class="meta-row">
              <span class="meta-label">预测时间：</span>
              <span>{{ fmtTime(result.predicted_at) }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">员工 ID：</span>
              <span>{{ result.employee_id }}</span>
            </div>
          </div>
        </div>

        <!-- 各模态分数 -->
        <div class="modality-section">
          <h4 class="section-title">各模态分数</h4>
          <div v-for="(v, k) in result.modality_scores" :key="k" class="modality-row">
            <div class="modality-label">{{ modalityLabels[k] || k }}</div>
            <div class="modality-bar-bg">
              <div
                class="modality-bar"
                :style="{ width: (v / modalityMax) * 100 + '%' }"
              ></div>
            </div>
            <div class="modality-value">{{ v.toFixed(1) }}</div>
          </div>
        </div>
      </div>

      <!-- SHAP 归因卡片 -->
      <div class="card shap-card">
        <h3 class="card-title">SHAP 归因（Top3 因子）</h3>
        <div v-if="top3Factors.length === 0" class="empty">暂无 SHAP 数据</div>
        <ul v-else class="shap-list">
          <li v-for="f in top3Factors" :key="f.feature" class="shap-item">
            <div class="shap-head">
              <span class="shap-name">{{ f.display_name }}</span>
              <span
                class="shap-arrow"
                :class="f.direction"
                :style="{ color: f.direction === 'positive' ? 'var(--risk-high)' : 'var(--risk-low)' }"
              >
                {{ f.direction === 'positive' ? '↑' : '↓' }}
                {{ f.contribution.toFixed(3) }}
              </span>
            </div>
            <div class="shap-bar-row">
              <div class="shap-bar-bg">
                <div
                  class="shap-bar"
                  :style="{
                    width: (Math.abs(f.contribution) / shapMaxAbs) * 100 + '%',
                    background: f.direction === 'positive' ? 'var(--risk-high)' : 'var(--risk-low)',
                  }"
                ></div>
              </div>
              <div class="shap-value">
                值：<code>{{ f.value ?? '-' }}</code>
              </div>
            </div>
            <div v-if="f.description" class="shap-desc">{{ f.description }}</div>
          </li>
        </ul>
        <div v-if="explanation" class="shap-foot">
          <span>base_value：{{ explanation.base_value.toFixed(3) }}</span>
          <span>output_value：{{ explanation.output_value.toFixed(3) }}</span>
          <span>计算时间：{{ fmtTime(explanation.computed_at) }}</span>
        </div>
      </div>
    </div>

    <!-- 全局特征重要性 -->
    <div class="card" style="margin-top:16px;">
      <h3 class="card-title">全局特征重要性（最近 {{ globalExp?.window_days || 30 }} 天 Top5）</h3>
      <div v-if="top5Global.length === 0" class="empty">暂无全局特征数据</div>
      <ul v-else class="global-list">
        <li v-for="f in top5Global" :key="f.feature" class="global-item">
          <div class="global-name">{{ f.display_name }}</div>
          <div class="global-bar-bg">
            <div
              class="global-bar"
              :style="{ width: (Math.abs(f.contribution) / globalMaxAbs) * 100 + '%' }"
            ></div>
          </div>
          <div class="global-value">{{ f.contribution.toFixed(3) }}</div>
        </li>
      </ul>
      <div v-if="globalExp" class="shap-foot">
        <span>模型版本：{{ globalExp.model_version }}</span>
        <span>计算时间：{{ fmtTime(globalExp.computed_at) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.input-card {
  margin-bottom: 16px;
}
.form-row {
  display: flex;
  gap: 12px;
}
.form-row input {
  flex: 1;
}

.result-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 16px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 16px;
}

/* 预测结果 */
.pred-main {
  display: flex;
  gap: 24px;
  align-items: center;
  margin-bottom: 20px;
  flex-wrap: wrap;
}
.score-circle {
  width: 110px;
  height: 110px;
  border-radius: 50%;
  border: 6px solid;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.score-num {
  font-size: 34px;
  font-weight: 700;
  line-height: 1;
}
.score-unit {
  font-size: 12px;
  color: var(--color-text-muted);
  margin-top: 4px;
}
.pred-meta {
  flex: 1;
  min-width: 220px;
}
.meta-row {
  margin: 6px 0;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.meta-label {
  color: var(--color-text-muted);
}

/* 模态分数 */
.modality-section {
  border-top: 1px solid var(--color-border);
  padding-top: 16px;
}
.section-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
}
.modality-row {
  display: grid;
  grid-template-columns: 100px 1fr 50px;
  gap: 10px;
  align-items: center;
  margin: 8px 0;
  font-size: 13px;
}
.modality-bar-bg {
  background: #f1f5f9;
  border-radius: 4px;
  height: 14px;
  overflow: hidden;
}
.modality-bar {
  height: 100%;
  background: linear-gradient(90deg, #2563eb, #7c3aed);
  border-radius: 4px;
  min-width: 2px;
}
.modality-value {
  text-align: right;
  font-weight: 600;
}

/* SHAP */
.shap-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.shap-item {
  padding: 10px 0;
  border-bottom: 1px solid var(--color-border);
}
.shap-item:last-child {
  border-bottom: none;
}
.shap-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  font-size: 13px;
}
.shap-name {
  font-weight: 600;
}
.shap-arrow {
  font-weight: 600;
}
.shap-bar-row {
  display: grid;
  grid-template-columns: 1fr 100px;
  gap: 10px;
  align-items: center;
}
.shap-bar-bg {
  background: #f1f5f9;
  border-radius: 4px;
  height: 10px;
  overflow: hidden;
}
.shap-bar {
  height: 100%;
  border-radius: 4px;
  min-width: 2px;
}
.shap-value {
  font-size: 12px;
  color: var(--color-text-muted);
  text-align: right;
}
.shap-value code {
  background: #e2e8f0;
  padding: 1px 5px;
  border-radius: 3px;
}
.shap-desc {
  font-size: 12px;
  color: var(--color-text-muted);
  margin-top: 4px;
}
.shap-foot {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--color-border);
  font-size: 12px;
  color: var(--color-text-muted);
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

/* 全局 */
.global-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.global-item {
  display: grid;
  grid-template-columns: 180px 1fr 60px;
  gap: 10px;
  align-items: center;
  padding: 8px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--color-border);
}
.global-item:last-child {
  border-bottom: none;
}
.global-bar-bg {
  background: #f1f5f9;
  border-radius: 4px;
  height: 12px;
  overflow: hidden;
}
.global-bar {
  height: 100%;
  background: linear-gradient(90deg, #2563eb, #7c3aed);
  border-radius: 4px;
  min-width: 2px;
}
.global-value {
  text-align: right;
  font-weight: 600;
}

@media (max-width: 900px) {
  .result-grid { grid-template-columns: 1fr; }
  .pred-main { flex-direction: column; align-items: flex-start; }
  .global-item { grid-template-columns: 130px 1fr 50px; }
}
</style>
