// API 类型定义（对齐后端 schemas）

export interface LoginResult {
  access_token: string
  refresh_token: string
  expires_in: number
  user: UserInfo
}

export interface UserInfo {
  id: string
  name: string
  role: string
  tenant_id: string
  email: string
}

export interface EmployeeListItem {
  id: string
  employee_no: string
  name_masked: string
  department_name: string | null
  position: string | null
  status: string
  risk_score: number | null
  risk_level: string | null
  updated_at: string
}

export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface WarningOut {
  id: string
  employee_id: string
  prediction_id: string | null
  level: 'P0' | 'P1' | 'P2'
  risk_score: number
  status: 'new' | 'confirmed' | 'review' | 'fixing' | 'appealing' | 'closed'
  assigned_to: string | null
  escalated_to: string | null
  message: string | null
  created_at: string
  confirmed_at: string | null
  closed_at: string | null
}

export interface RiskPredictionOut {
  prediction_id: string
  employee_id: string
  risk_score: number
  risk_level: string
  modality_scores: Record<string, number>
  model_version: string
  predicted_at: string
  cached: boolean
}

// ===== W6 阶段新增类型 =====

// SHAP 单因子（D05 3.4 GET /risk/employees/{id}/explanation）
export interface ShapFactor {
  feature: string
  display_name: string
  value?: number | string
  contribution: number
  direction: 'positive' | 'negative'
  description?: string
}

// 单样本 SHAP 解释结果
export interface ShapExplanationOut {
  prediction_id: string
  factors: ShapFactor[]
  base_value: number
  output_value: number
  computed_at: string
}

// 全局特征重要性
export interface GlobalExplanationOut {
  model_version: string
  window_days: number
  top_features: ShapFactor[]
  computed_at: string
}

// Kill Switch 状态（D05 3.6 GET /admin/kill-switch）
export interface KillSwitchStatus {
  active: boolean
  reason?: string | null
  activated_at?: string | null
  activated_by?: string | null
}

// 预警状态机变更请求（PATCH /warnings/{id}/status）
export interface WarningStatusUpdate {
  target_status: WarningOut['status']
  operator_id: string
  comment?: string
}

// 预警申诉请求（POST /warnings/{id}/appeal）
export interface AppealRequest {
  reason: string
  operator_id: string
}

// 预警标记请求（POST /warnings/{id}/mark）
export interface MarkRequest {
  mark_type: 'false_positive' | 'watching' | 'communicated'
  comment: string
  operator_id: string
}

// 健康检查响应（GET /health）
export interface HealthOut {
  status: string
  version: string
  env: string
  components: Record<string, unknown>
}

// 漂移检测结果（治理视图占位类型，后端实际契约可对接）
export interface DriftResult {
  max_psi: number
  critical_features: string[]
  features: Array<{ feature: string; psi: number }>
  computed_at: string
}

// 公平性监测结果（治理视图占位类型）
export interface FairnessResult {
  dimensions: Array<{
    name: 'gender' | 'age' | 'ethnicity' | 'disability'
    label: string
    disparity: number // 百分比
  }>
  computed_at: string
}

// SSE 元数据（advise/stream）
export interface AdviseMetadata {
  model?: string
  tokens_used?: number
  latency_ms?: number
  [key: string]: unknown
}
