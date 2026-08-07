/* ═══════════════════════════════════════════════════════════════
   KeysView 私有工具 —— 异常标签 / 并发执行器 / Key 文本解析
   （与旧版 docs/archive/dashboard-legacy.html 中的实现一一对应）
   ═══════════════════════════════════════════════════════════════ */

/** 异常 flag → 中文标签（对齐旧版 anomalyBadges） */
export const ANOMALY_LABELS: Record<string, string> = {
  exhausted: '耗尽',
  near_exhausted: '近耗尽',
  suspected_leak: '疑似泄露',
  high_error_rate: '高错误率',
  stale: '静默',
  slow: '慢',
}

export function anomalyLabel(flag: string): string {
  return ANOMALY_LABELS[flag] ?? flag
}

/** 严重级别较高的 flag（明细里用危险色徽章） */
export const SEVERE_FLAGS: ReadonlySet<string> = new Set([
  'exhausted',
  'suspected_leak',
  'high_error_rate',
])

/**
 * 并行执行器：以固定并发数处理任务列表（移植自旧版 runParallel）。
 * worker 内部自行捕获异常，这里只对漏网之鱼兜底。
 */
export async function runParallel<T>(
  items: readonly T[],
  worker: (item: T, index: number) => Promise<void>,
  concurrency = 5,
): Promise<void> {
  const total = items.length
  if (total === 0) return
  const n = Math.max(1, Math.min(concurrency, total))
  let idx = 0
  async function runner(): Promise<void> {
    for (;;) {
      const i = idx++
      if (i >= total) break
      try {
        await worker(items[i], i)
      } catch (e) {
        console.error(e)
      }
    }
  }
  await Promise.all(Array.from({ length: n }, () => runner()))
}

/**
 * 解析批量添加的 Key 文本：每行一个，或逗号/分号/空白分隔；去重、去空。
 * （旧版仅按行分隔，这里按任务要求兼容逗号分隔）
 */
export function parseKeysText(raw: string): string[] {
  const parts = raw
    .split(/[\s,，;；]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  return Array.from(new Set(parts))
}

/** 从 unknown 异常提取可读消息 */
export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/**
 * 全量操作（健康检测 / 用量同步）逐 Key 结果，进度模态展示用。
 * ok 正常 / bad 异常（业务失败）/ skip 跳过 / fail 失败（请求层错误）
 */
export interface BulkItemResult {
  masked: string
  status: 'ok' | 'bad' | 'skip' | 'fail'
  detail: string
}
