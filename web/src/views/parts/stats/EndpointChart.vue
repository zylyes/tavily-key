<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import GlassCard from '@/components/GlassCard.vue'
import Skeleton from '@/components/Skeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useECharts, chartColors } from '@/composables/useECharts'
import { fmtNum } from '@/utils/format'
import type { TrendPoint } from '@/api/client'
import { chartEntrance, chartPalette, glassTooltip } from './chartCommon'

/* 接口分布图 —— 按 endpoint 聚合请求量（横向柱状，随天数与来源联动）
   数据源：趋势点 endpoints 字段（趋势接口已按 source/days 过滤）。
   注：趋势接口不提供 per-endpoint 的成功/失败拆分，故展示总请求量。 */
const props = defineProps<{
  points: TrendPoint[] | null
  loading: boolean
  days: number
}>()

const el = ref<HTMLElement | null>(null)
const hasDrawn = ref(false)

const agg = computed(() => {
  const m = new Map<string, number>()
  for (const p of props.points ?? []) {
    for (const [ep, n] of Object.entries(p.endpoints ?? {})) m.set(ep, (m.get(ep) ?? 0) + n)
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10)
})
const total = computed(() => agg.value.reduce((s, [, n]) => s + n, 0))
const isEmpty = computed(() => agg.value.length === 0)

const { update } = useECharts(el, () => {
  const c = chartColors()
  const palette = chartPalette()
  const rows = agg.value
  return {
    ...chartEntrance(!hasDrawn.value),
    grid: { left: 8, right: 36, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      ...glassTooltip('item'),
      formatter: (params: unknown) => {
        const p = params as { marker?: string; name: string; value: number }
        const pct = total.value ? ((p.value / total.value) * 100).toFixed(1) : '0.0'
        return `${p.marker ?? ''}${p.name}：${fmtNum(p.value)} 次（${pct}%）`
      },
    },
    xAxis: {
      type: 'value' as const,
      minInterval: 1,
      axisLabel: { color: c.textDim, fontSize: 10.5 },
      splitLine: { lineStyle: { color: c.border, type: 'dashed' as const } },
    },
    yAxis: {
      type: 'category' as const,
      inverse: true,
      data: rows.map(([name]) => name),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: c.text, fontSize: 11.5, width: 92, overflow: 'truncate' as const },
    },
    series: [
      {
        type: 'bar' as const,
        barMaxWidth: 14,
        data: rows.map(([name, n], i) => ({
          value: n,
          name,
          itemStyle: {
            color: palette[i % palette.length] ?? c.accent,
            borderRadius: [0, 7, 7, 0],
          },
        })),
        label: {
          show: true,
          position: 'right' as const,
          distance: 6,
          color: c.textDim,
          fontSize: 10.5,
        },
        emphasis: { itemStyle: { opacity: 0.85 } },
      },
    ],
  }
})

watch(
  () => props.points,
  () => {
    update()
    if (!isEmpty.value) hasDrawn.value = true
  },
  { flush: 'post' },
)
</script>

<template>
  <GlassCard title="接口分布" :desc="`各接口请求量 · 近 ${days} 天 · 随来源联动`">
    <Skeleton v-if="loading" height="260px" />
    <EmptyState
      v-else-if="isEmpty"
      icon="list"
      title="暂无接口请求"
      desc="所选时间范围与来源内没有请求记录"
    />
    <div v-show="!loading && !isEmpty" ref="el" class="ep-chart"></div>
  </GlassCard>
</template>

<style scoped>
.ep-chart { width: 100%; height: 260px; }
</style>
