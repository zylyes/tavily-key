/* ═══════════════════════════════════════════════════════════════
   format.ts —— 视图通用格式化助手（与旧版 fmt* 行为对齐）
   ═══════════════════════════════════════════════════════════════ */

/** unix 秒 → 'MM-DD HH:mm'（本地时区）；0/空 → '-' */
export function fmtTs(ts: number | null | undefined): string {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** unix 秒 → 完整 'YYYY-MM-DD HH:mm:ss' */
export function fmtTsFull(ts: number | null | undefined): string {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 毫秒 → '0ms' / '850ms' / '1.2s' */
export function fmtLatency(ms: number | null | undefined): string {
  const x = ms ?? 0
  if (x === 0) return '0ms'
  if (x < 1000) return `${x.toFixed(0)}ms`
  return `${(x / 1000).toFixed(1)}s`
}

/** 千分位数字：1234567 → '1,234,567' */
export function fmtNum(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString('en-US')
}

/** 百分比：12.345 → '12.3%' */
export function fmtPct(pct: number | null | undefined, digits = 1): string {
  return `${(pct ?? 0).toFixed(digits)}%`
}

/**
 * 日志「积分」列语义（与旧版 fmtCredits 对齐）：
 * 失败请求 → '-'；接口不返回 usage（Research 等）→ '—'；否则数值。
 */
export function fmtCredits(log: { success: number | boolean; usage_source?: string; credits_consumed: number }): string {
  if (!log.success) return '-'
  if (log.usage_source === 'unknown') return '—'
  return String(log.credits_consumed)
}

/** 字节数 → '1.2 MB' */
export function fmtBytes(n: number | null | undefined): string {
  const x = n ?? 0
  if (x < 1024) return `${x} B`
  if (x < 1024 ** 2) return `${(x / 1024).toFixed(1)} KB`
  if (x < 1024 ** 3) return `${(x / 1024 ** 2).toFixed(1)} MB`
  return `${(x / 1024 ** 3).toFixed(2)} GB`
}
