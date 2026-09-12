// fmtTime 单测：空值 / 非法日期串 / 合法 ISO
import { describe, expect, it } from 'vitest'
import { fmtTime } from '@/utils/format'

describe('fmtTime', () => {
  it('空值（null/undefined/空串）返回占位符 -', () => {
    expect(fmtTime(null)).toBe('-')
    expect(fmtTime(undefined)).toBe('-')
    expect(fmtTime('')).toBe('-')
  })

  it('非法日期串原样返回（不输出 "Invalid Date"）', () => {
    expect(fmtTime('not-a-date')).toBe('not-a-date')
  })

  it('合法 ISO 时间返回本地化字符串（非占位符）', () => {
    const out = fmtTime(new Date(0).toISOString())
    expect(out).not.toBe('-')
    expect(typeof out).toBe('string')
    expect(out.length).toBeGreaterThan(0)
  })
})
