// 行为事件上报 API（前端埋点管道）
//
// 设计：
//   - 缓冲式批量上报：事件先入内存队列，5s 或满 20 条即批量 POST /behavior/events；
//   - best-effort：未登录 / 请求失败静默丢弃（不阻塞业务、不弹错）；
//   - 页面卸载前用 sendBeacon 兜底冲刷，避免最后一跳丢失。
import { apiClient } from '@/api/client'

export interface BehaviorEventPayload {
  event_type: string
  employee_id?: string | null
  payload?: Record<string, unknown>
  occurred_at?: string
}

const FLUSH_INTERVAL_MS = 5000
const FLUSH_MAX_BATCH = 20

let queue: BehaviorEventPayload[] = []
let flushTimer: ReturnType<typeof setInterval> | null = null
let pageHiddenHandler: (() => void) | null = null

/** 入队一条行为事件（页面级/功能级均可） */
export function trackBehavior(event: BehaviorEventPayload): void {
  queue.push(event)
  ensureFlushTimer()
  if (queue.length >= FLUSH_MAX_BATCH) {
    void flushNow()
  }
}

/** 页面可见性丢失时兜底冲刷（sendBeacon 可在卸载前发出） */
function ensurePageHiddenFlush(): void {
  if (pageHiddenHandler) return
  pageHiddenHandler = () => {
    // 隐藏即冲刷（移动端切后台/关闭页面），减少丢失窗口
    const pending = drainQueue()
    if (pending.length === 0) return
    const body = JSON.stringify({ events: pending })
    if (typeof navigator !== 'undefined' && 'sendBeacon' in navigator) {
      navigator.sendBeacon('/api/v1/behavior/events', body)
    }
  }
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', pageHiddenHandler)
  }
}

function ensureFlushTimer(): void {
  if (flushTimer) return
  flushTimer = setInterval(() => {
    void flushNow()
  }, FLUSH_INTERVAL_MS)
  ensurePageHiddenFlush()
  // 定时器不阻止进程退出
  if (flushTimer.unref) flushTimer.unref()
}

function drainQueue(): BehaviorEventPayload[] {
  const pending = queue
  queue = []
  return pending
}

/** 立即冲刷队列（供测试调用；返回实际 POST 的条数） */
export async function flushNow(): Promise<number> {
  const pending = drainQueue()
  if (pending.length === 0) return 0
  try {
    await apiClient.post('/api/v1/behavior/events', { events: pending })
    return pending.length
  } catch {
    // best-effort：失败丢弃本批（避免无限重试放大日志噪音）
    return 0
  }
}

/** 测试辅助：清空队列与定时器 */
export function _resetTracker(): void {
  queue = []
  if (flushTimer) {
    clearInterval(flushTimer)
    flushTimer = null
  }
  if (pageHiddenHandler && typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', pageHiddenHandler)
    pageHiddenHandler = null
  }
}
