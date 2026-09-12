// 跨视图共享的格式化工具（单一事实来源，替代各视图复制的 fmtTime）
export function fmtTime(s?: string | null): string {
  if (!s) return '-'
  try {
    const d = new Date(s)
    // 非法日期串不会抛异常，而是产生 Invalid Date（getTime 为 NaN）
    if (Number.isNaN(d.getTime())) return s
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return s
  }
}
