import { onBeforeUnmount, onMounted, shallowRef, type Ref } from 'vue'
import * as echarts from 'echarts'

/* ═══════════════════════════════════════════════════════════════
   useECharts —— ECharts 5 封装：初始化 / 主题联动 / 自适应 / 释放
   echarts 已在 package.json（^5），视图按需 import 的图型也可，
   但直接用本封装即可（全量打包，离线桌面应用无体积敏感）。

   典型用法：
     const el = ref<HTMLElement | null>(null)
     const { update } = useECharts(el, () => ({
       grid: {...}, xAxis: {...},
       series: [{ type: 'line', data: trend.value?.points.map(...) }]
     }))
     watch(trend, () => update())
   ═══════════════════════════════════════════════════════════════ */

/** 读取当前主题的 CSS 变量（构造 option 时取色用）。 */
export function cssVar(name: string, fallback = ''): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

/** 图表常用色板（每次调用实时读取，主题切换后 update() 即生效）。 */
export function chartColors() {
  return {
    accent: cssVar('--accent', '#7c8cff'),
    accent2: cssVar('--accent-2', '#a78bfa'),
    success: cssVar('--success', '#34d399'),
    warn: cssVar('--warn', '#fbbf24'),
    danger: cssVar('--danger', '#f87171'),
    info: cssVar('--info', '#60a5fa'),
    text: cssVar('--text-2', '#8a90a8'),
    textDim: cssVar('--text-3', '#5a6078'),
    border: cssVar('--glass-border', 'rgba(128,128,160,.2)'),
  }
}

export interface UseEChartsResult {
  /** ECharts 实例（挂载后为 null 需容错） */
  instance: Ref<echarts.ECharts | null>
  /** 重新调用 option 工厂并 setOption（数据变化后调用） */
  update: () => void
  resize: () => void
}

export function useECharts(
  el: Ref<HTMLElement | null>,
  makeOption: () => echarts.EChartsOption,
): UseEChartsResult {
  const instance = shallowRef<echarts.ECharts | null>(null)
  let resizeObserver: ResizeObserver | null = null
  let themeObserver: MutationObserver | null = null

  function update(): void {
    if (instance.value) instance.value.setOption(makeOption(), true)
  }

  function resize(): void {
    instance.value?.resize()
  }

  onMounted(() => {
    if (!el.value) return
    instance.value = echarts.init(el.value)
    update()
    resizeObserver = new ResizeObserver(() => instance.value?.resize())
    resizeObserver.observe(el.value)
    // 主题切换（<html data-theme> 变化）→ 重新取色
    themeObserver = new MutationObserver(() => update())
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    })
  })

  onBeforeUnmount(() => {
    themeObserver?.disconnect()
    resizeObserver?.disconnect()
    instance.value?.dispose()
    instance.value = null
  })

  return { instance, update, resize }
}
