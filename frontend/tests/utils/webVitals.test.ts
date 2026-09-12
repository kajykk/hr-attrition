// Web Vitals 工具测试：阈值判定、observer 容错
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ratingFor, initWebVitals } from '@/utils/webVitals';

describe('ratingFor', () => {
  it('LCP 阈值：2500ms good / 4000ms needs-improvement / 以上 poor', () => {
    expect(ratingFor('LCP', 1000)).toBe('good');
    expect(ratingFor('LCP', 3000)).toBe('needs-improvement');
    expect(ratingFor('LCP', 5000)).toBe('poor');
  });

  it('INP 阈值：200ms good / 500ms needs-improvement / 以上 poor', () => {
    expect(ratingFor('INP', 150)).toBe('good');
    expect(ratingFor('INP', 300)).toBe('needs-improvement');
    expect(ratingFor('INP', 600)).toBe('poor');
  });

  it('CLS 分数阈值：0.1 good / 0.25 needs-improvement / 以上 poor', () => {
    expect(ratingFor('CLS', 0.05)).toBe('good');
    expect(ratingFor('CLS', 0.2)).toBe('needs-improvement');
    expect(ratingFor('CLS', 0.5)).toBe('poor');
  });

  it('TTFB 阈值：800ms good / 1800ms needs-improvement / 以上 poor', () => {
    expect(ratingFor('TTFB', 300)).toBe('good');
    expect(ratingFor('TTFB', 1200)).toBe('needs-improvement');
    expect(ratingFor('TTFB', 2500)).toBe('poor');
  });
});

describe('initWebVitals', () => {
  // jsdom 无 PerformanceObserver，应静默降级不抛错
  it('无 PerformanceObserver 环境静默降级，不抛异常', () => {
    expect(() => initWebVitals(() => {})).not.toThrow();
  });

  it('返回可调用的清理函数', () => {
    const cleanup = initWebVitals(() => {});
    expect(typeof cleanup).toBe('function');
    expect(() => cleanup()).not.toThrow();
  });

  it('TTFB 在无 navigation entry 时静默跳过', () => {
    const spy = vi.fn();
    initWebVitals(spy);
    // jsdom 无真实 navigation entry，默认 reporter 不应抛错
    expect(spy).not.toHaveBeenCalled();
  });
});