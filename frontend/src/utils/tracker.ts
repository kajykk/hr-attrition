// 前端埋点工具：页面停留 + 功能使用事件采集
//
// 语义（对齐后端行为特征管道）：
//   - page_view：路由进入 → 离开（含 visibilitychange 后台/前台）期间累计停留毫秒；
//   - feature_use：关键功能触发（报表导出、预测查看等）；
//   - 事件经 behavior.ts 缓冲批量上报；当前用户为员工时才落库（管理账号由服务端过滤）。
import { trackBehavior } from '@/api/behavior'

export interface TrackerOptions {
  /** 显式员工上下文（员工详情页等可传入） */
  employeeId?: string | null
}

// 页面停留累计（同一路由会话内多段可见时间累加）
interface DwellAccumulator {
  routeName: string
  routePath: string
  totalMs: number
  lastVisibleAt: number | null
  employeeId?: string | null
}

let dwell: DwellAccumulator | null = null

function nowMs(): number {
  return Date.now()
}

function isDocumentVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden'
}

/** 路由进入：启动停留计时 */
export function startRouteDwell(routeName: string, routePath: string, opts: TrackerOptions = {}): void {
  // 忽略重复进入（同一路由守卫触发两次）
  if (dwell && dwell.routeName === routeName) return
  endRouteDwell()
  dwell = {
    routeName,
    routePath,
    totalMs: 0,
    lastVisibleAt: isDocumentVisible() ? nowMs() : null,
    employeeId: opts.employeeId,
  }
}

/** 路由离开（或切换到后台）：结算当前段并累计 */
export function pauseRouteDwell(): void {
  if (!dwell || dwell.lastVisibleAt === null) return
  dwell.totalMs += nowMs() - dwell.lastVisibleAt
  dwell.lastVisibleAt = null
}

/** 回到前台：恢复计时 */
export function resumeRouteDwell(): void {
  if (!dwell) return
  dwell.lastVisibleAt = nowMs()
}

/** 路由离开：结算并上报 page_view 事件（停留 < 1s 视为误触，丢弃） */
export function endRouteDwell(): void {
  if (!dwell) return
  pauseRouteDwell()
  const d = dwell
  dwell = null
  // 阈值 1s：过滤快速导航/刷新抖动，避免污染行为特征
  if (d.totalMs < 1000) return
  trackBehavior({
    event_type: 'ui_page_view',
    employee_id: d.employeeId,
    payload: {
      route: d.routePath,
      route_name: d.routeName,
      duration_ms: d.totalMs,
    },
  })
}

/** 功能使用事件（报表导出、预测查看等） */
export function trackFeatureUse(
  feature: string,
  opts: TrackerOptions & { extra?: Record<string, unknown> } = {},
): void {
  trackBehavior({
    event_type: 'ui_feature_use',
    employee_id: opts.employeeId,
    payload: { feature, ...(opts.extra ?? {}) },
  })
}

/** 报表查看事件（进入某员工的预测/预警详情） */
export function trackReportView(
  reportType: string,
  employeeId: string | null,
  extra: Record<string, unknown> = {},
): void {
  trackBehavior({
    event_type: 'ui_report_view',
    employee_id: employeeId,
    payload: { report_type: reportType, ...extra },
  })
}

/** 测试辅助：重置停留状态 */
export function _resetDwell(): void {
  dwell = null
}
