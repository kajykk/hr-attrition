/* global PerformanceEntryList, PerformanceEventTiming */
/**
 * Web Vitals 采集与上报
 *
 * 使用 PerformanceObserver 采集 Core Web Vitals（LCP / FID→INP / CLS / TTFB），
 * 通过可注入的 reporter 上报（默认 console，可替换为 Sentry / 自建埋点）。
 *
 * 用法：
 *   import { initWebVitals } from '@/utils/webVitals'
 *   initWebVitals() // main.ts 中调用
 *
 * 上报数据（JSON）：
 *   { name, value, rating, delta, id }
 *   - name: LCP | INP | CLS | TTFB | FCP
 *   - value: 毫秒（CLS 为无量纲分数）
 *   - rating: good | needs-improvement | poor（web-vitals 阈值）
 */
import { onMounted } from 'vue'

export interface WebVitalMetric {
  name: string
  value: number
  rating: 'good' | 'needs-improvement' | 'poor'
  delta?: number
  id?: string
}

export type WebVitalsReporter = (metric: WebVitalMetric) => void

// 默认 reporter：仅生产环境输出到 console（开发环境静默，避免噪音）
const defaultReporter: WebVitalsReporter = (metric) => {
  if (import.meta.env.PROD) {
    console.info(`[WebVitals] ${metric.name}: ${metric.value.toFixed(1)} (${metric.rating})`)
  }
}

/** 阈值判定（与 web-vitals 库一致，单位为毫秒；CLS 为分数） */
export function ratingFor(name: string, value: number): WebVitalMetric['rating'] {
  if (name === 'CLS') {
    if (value <= 0.1) return 'good'
    if (value <= 0.25) return 'needs-improvement'
    return 'poor'
  }
  // 时间类指标（LCP/FCP/INP/TTFB）
  const good = name === 'LCP' ? 2500 : name === 'INP' ? 200 : name === 'TTFB' ? 800 : 1800
  const poor = name === 'LCP' ? 4000 : name === 'INP' ? 500 : name === 'TTFB' ? 1800 : 3000
  if (value <= good) return 'good'
  if (value <= poor) return 'needs-improvement'
  return 'poor'
}

/** 通过 PerformanceObserver 采集指标，回调上报 */
function observe(name: string, type: string, callback: (entries: PerformanceEntryList) => void): void {
  if (typeof PerformanceObserver === 'undefined') return
  try {
    const observer = new PerformanceObserver((list) => callback(list.getEntries()))
    observer.observe({ type, buffered: true })
  } catch {
    // 浏览器不支持该指标类型时静默跳过
  }
}

/** LCP：最后一次 contentful paint */
function watchLCP(report: WebVitalsReporter): void {
  let lastValue = 0
  observe('LCP', 'largest-contentful-paint', (entries) => {
    const entry = entries[entries.length - 1] as PerformanceEntry & { startTime: number }
    if (!entry) return
    lastValue = entry.startTime
    report({ name: 'LCP', value: lastValue, rating: ratingFor('LCP', lastValue) })
  })
  // LCP 延迟上报：确保取到最终值（LCP 可能多次更新）
  window.addEventListener('load', () => {
    setTimeout(() => {
      if (lastValue > 0) {
        report({ name: 'LCP', value: lastValue, rating: ratingFor('LCP', lastValue) })
      }
    }, 0)
  })
}

/** FCP：首次内容绘制 */
function watchFCP(report: WebVitalsReporter): void {
  observe('FCP', 'paint', (entries) => {
    const entry = entries.find((e) => e.name === 'first-contentful-paint')
    if (!entry) return
    report({ name: 'FCP', value: entry.startTime, rating: ratingFor('FCP', entry.startTime) })
  })
}

/** INP：交互到下一次绘制（替代 FID 的现代指标） */
function watchINP(report: WebVitalsReporter): void {
  observe('INP', 'event', (entries) => {
    const last = entries[entries.length - 1] as PerformanceEventTiming | undefined
    if (!last || last.duration <= 0) return
    report({ name: 'INP', value: last.duration, rating: ratingFor('INP', last.duration) })
  })
}

/** CLS：累计布局偏移 */
function watchCLS(report: WebVitalsReporter): void {
  let cls = 0
  observe('CLS', 'layout-shift', (entries) => {
    for (const e of entries) {
      const entry = e as PerformanceEntry & { hadRecentInput: boolean; value: number }
      if (!entry.hadRecentInput) cls += entry.value
    }
    report({ name: 'CLS', value: cls, rating: ratingFor('CLS', cls) })
  })
}

/** TTFB：首字节时间 */
function watchTTFB(report: WebVitalsReporter): void {
  const nav = performance.getEntriesByType('navigation')[0] as
    | (PerformanceNavigationTiming & { responseStart: number; requestStart: number })
    | undefined
  if (!nav) return
  const ttfb = nav.responseStart - nav.requestStart
  report({ name: 'TTFB', value: ttfb, rating: ratingFor('TTFB', ttfb) })
}

/**
 * 初始化 Web Vitals 采集。
 * @param reporter 自定义上报函数（默认 console）
 * @returns 清理函数（卸载 observer）
 */
export function initWebVitals(reporter?: WebVitalsReporter): () => void {
  const report = reporter ?? defaultReporter
  // 若显式传入 reporter，其接收全部指标；默认 reporter 仅在 PROD 输出
  watchLCP(report)
  watchFCP(report)
  watchINP(report)
  watchCLS(report)
  watchTTFB(report)
  return () => {
    // PerformanceObserver 在页面生命周期内常驻，无需显式清理；
    // 返回 no-op 保持接口一致（测试/热重载场景可调用）
  }
}

/** 供 Vue 组件/页面在 onMounted 后便捷初始化 */
export function useWebVitals(reporter?: WebVitalsReporter): void {
  onMounted(() => {
    initWebVitals(reporter)
  })
}