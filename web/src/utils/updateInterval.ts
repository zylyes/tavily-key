/* updateInterval —— 更新检查间隔的纯换算函数（SettingsView 与单测共用）。

   面板展示单位：小时/日/星期/月（月按 30 天计）；后端生效值始终是小时数
   （update_check_interval_hours）。本模块保持纯函数、无副作用，便于单元测试。
*/
export const UNIT_HOURS: Record<string, number> = { hour: 1, day: 24, week: 168, month: 720 }
/** 检查间隔上限：无论什么单位最多一年（365 天 = 8760 小时） */
export const MAX_YEAR_HOURS = 365 * 24
export type IntervalUnit = 'hour' | 'day' | 'week' | 'month'
export const INTERVAL_UNITS: IntervalUnit[] = ['hour', 'day', 'week', 'month']

/** 后端小时数 → 面板展示值 + 单位。
 *  无法整除时回退小时单位展示；0=旧版关闭值回退默认 24 小时。 */
export function intervalToDisplay(hours: number, unit: string): { value: string; unit: IntervalUnit } {
  const safe = hours > 0 ? hours : 24
  const u = (UNIT_HOURS[unit] ? unit : 'hour') as IntervalUnit
  const v = safe / UNIT_HOURS[u]
  if (!Number.isInteger(v)) {
    return { value: String(safe), unit: 'hour' }
  }
  return { value: String(v), unit: u }
}

/** 面板输入（数值字符串 + 单位）→ 小时数；非法输入返回 -1。 */
export function displayToHours(value: string, unit: string): number {
  const v = parseFloat(value.trim())
  if (Number.isNaN(v) || v < 0) return -1
  return v * (UNIT_HOURS[unit] ?? 1)
}

/** 单位切换：保持间隔不变（原值 × 原单位 ÷ 新单位，四舍五入 2 位）；非法输入原样返回。 */
export function convertUnit(value: string, from: string, to: string): string {
  const v = parseFloat(value.trim())
  if (Number.isNaN(v) || v < 0) return value
  const hours = v * (UNIT_HOURS[from] ?? 1)
  return String(Math.round((hours / (UNIT_HOURS[to] ?? 1)) * 100) / 100)
}
