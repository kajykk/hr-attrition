// @vitest-environment jsdom
// 行为埋点管道单测：tracker 事件采集 + behavior.ts 缓冲批量上报
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const postMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', () => ({
  apiClient: {
    post: (...args: unknown[]) => postMock(...args),
  },
}))

import {
  _resetTracker,
  flushNow,
  trackBehavior,
} from '@/api/behavior'
import {
  _resetDwell,
  endRouteDwell,
  pauseRouteDwell,
  resumeRouteDwell,
  startRouteDwell,
  trackFeatureUse,
  trackReportView,
} from '@/utils/tracker'

describe('behavior.ts 缓冲批量上报', () => {
  beforeEach(() => {
    _resetTracker()
    _resetDwell()
    vi.useFakeTimers()
    postMock.mockReset()
    postMock.mockResolvedValue({ data: {} })
  })

  afterEach(() => {
    _resetTracker()
    _resetDwell()
    vi.useRealTimers()
  })

  it('trackBehavior 入队后手动冲刷 → 批量 POST 一次', async () => {
    trackBehavior({ event_type: 'ui_page_view', payload: { route: '/risk' } })
    trackBehavior({ event_type: 'ui_feature_use', payload: { feature: 'export' } })

    const n = await flushNow()
    expect(n).toBe(2)
    expect(postMock).toHaveBeenCalledTimes(1)
    const [url, body] = postMock.mock.calls[0]
    expect(url).toBe('/api/v1/behavior/events')
    expect((body as { events: unknown[] }).events).toHaveLength(2)
  })

  it('空队列冲刷为 no-op（不发请求）', async () => {
    const n = await flushNow()
    expect(n).toBe(0)
    expect(postMock).not.toHaveBeenCalled()
  })

  it('超过批量上限（20 条）自动触发冲刷', async () => {
    for (let i = 0; i < 20; i++) {
      trackBehavior({ event_type: 'ui_page_view', payload: { i } })
    }
    await vi.runOnlyPendingTimersAsync()
    expect(postMock).toHaveBeenCalledTimes(1)
    expect((postMock.mock.calls[0][1] as { events: unknown[] }).events).toHaveLength(20)
  })

  it('发送失败静默丢弃（best-effort，不抛错）', async () => {
    postMock.mockRejectedValue(new Error('network down'))
    trackBehavior({ event_type: 'ui_page_view' })
    const n = await flushNow()
    expect(n).toBe(0)
  })
})

describe('tracker.ts 页面停留采集', () => {
  beforeEach(() => {
    _resetTracker()
    _resetDwell()
    vi.useFakeTimers()
    postMock.mockReset()
    postMock.mockResolvedValue({ data: {} })
  })

  afterEach(() => {
    _resetTracker()
    _resetDwell()
    vi.useRealTimers()
  })

  it('页面停留 ≥1s 上报 page_view 且携带停留时长', async () => {
    startRouteDwell('risk', '/risk')
    vi.advanceTimersByTime(5000)
    endRouteDwell()
    await flushNow()

    const body = postMock.mock.calls[0][1] as { events: Array<{ event_type: string; payload: Record<string, unknown> }> }
    expect(body.events[0].event_type).toBe('ui_page_view')
    expect(body.events[0].payload.duration_ms).toBe(5000)
    expect(body.events[0].payload.route).toBe('/risk')
  })

  it('停留不足 1s 视为误触 → 丢弃不上报', async () => {
    startRouteDwell('dashboard', '/dashboard')
    vi.advanceTimersByTime(500)
    endRouteDwell()
    await flushNow()

    expect(postMock).not.toHaveBeenCalled()
  })

  it('切后台暂停计时、回前台恢复（visibility 分段时间累计）', async () => {
    startRouteDwell('risk', '/risk')
    vi.advanceTimersByTime(2000)
    pauseRouteDwell() // 模拟切后台
    vi.advanceTimersByTime(9000) // 后台停留不计入
    resumeRouteDwell() // 回前台
    vi.advanceTimersByTime(3000)
    endRouteDwell()
    await flushNow()

    const body = postMock.mock.calls[0][1] as { events: Array<{ payload: Record<string, unknown> }> }
    expect(body.events[0].payload.duration_ms).toBe(5000) // 2000 + 3000，后台 9000 不计
  })

  it('重复 startRouteDwell 同路由不重置计时（守卫重复触发）', async () => {
    startRouteDwell('risk', '/risk')
    vi.advanceTimersByTime(2000)
    startRouteDwell('risk', '/risk') // 重复触发
    vi.advanceTimersByTime(2000)
    endRouteDwell()
    await flushNow()

    const body = postMock.mock.calls[0][1] as { events: Array<{ payload: Record<string, unknown> }> }
    expect(body.events[0].payload.duration_ms).toBe(4000)
  })

  it('功能使用事件携带 feature 名与员工上下文', async () => {
    trackFeatureUse('report_export', { employeeId: 'emp-1', extra: { rows: 100 } })
    await flushNow()

    const body = postMock.mock.calls[0][1] as { events: Array<Record<string, unknown>> }
    expect(body.events[0].event_type).toBe('ui_feature_use')
    expect(body.events[0].employee_id).toBe('emp-1')
    expect((body.events[0].payload as Record<string, unknown>).feature).toBe('report_export')
  })

  it('报表查看事件（详情页进入）', async () => {
    trackReportView('prediction', 'emp-9', { risk_score: 72 })
    await flushNow()

    const body = postMock.mock.calls[0][1] as { events: Array<Record<string, unknown>> }
    expect(body.events[0].event_type).toBe('ui_report_view')
    expect(body.events[0].employee_id).toBe('emp-9')
  })
})
