/* ═══════════════════════════════════════════════════════════════
   chartCommon —— 统计页图表共享助手（玻璃 tooltip / 调色板 / 透明度）
   取色全部实时读 CSS 变量，配合 useECharts 的主题观察器自动联动。
   ═══════════════════════════════════════════════════════════════ */
import { chartColors, cssVar } from '@/composables/useECharts'

/** 统一入场动效：仅首绘播放（800ms cubicOut），轮询静默刷新即时替换、不重放 */
export function chartEntrance(first: boolean) {
  return first
    ? { animation: true as const, animationDuration: 800, animationEasing: 'cubicOut' as const }
    : { animation: false as const }
}

/** 玻璃拟态 tooltip（背景/描边/模糊均取 CSS 变量） */
export function glassTooltip(trigger: 'axis' | 'item') {
  const blur = cssVar('--glass-blur', 'blur(18px) saturate(1.5)')
  return {
    trigger,
    backgroundColor: cssVar('--glass-bg-2', 'rgba(16, 20, 34, .88)'),
    borderColor: cssVar('--glass-border-strong', 'rgba(128, 128, 160, .3)'),
    borderWidth: 1,
    padding: [9, 12] as [number, number],
    textStyle: { color: cssVar('--text', '#e9edf7'), fontSize: 12 },
    extraCssText:
      `backdrop-filter:${blur};-webkit-backdrop-filter:${blur};` +
      'border-radius:var(--r-ctrl);box-shadow:var(--shadow-pop);',
  }
}

/** '#rgb' / '#rrggbb' → 'rgba(...)'；非 hex 颜色原样返回 */
export function withAlpha(color: string, alpha: number): string {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim())
  if (!m || !m[1]) return color
  let h = m[1]
  if (h.length === 3) h = [...h].map((ch) => ch + ch).join('')
  const n = Number.parseInt(h, 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

/** 分类色板（接口分布等多类目图表用；每次调用实时取色） */
export function chartPalette(): string[] {
  const c = chartColors()
  return [c.accent, c.info, c.success, c.accent2, c.warn, c.danger]
}
