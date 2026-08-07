<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import GlassCard from '@/components/GlassCard.vue'
import Skeleton from '@/components/Skeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useECharts, chartColors } from '@/composables/useECharts'
import type { TrendPoint } from '@/api/client'
import { chartEntrance, glassTooltip, withAlpha } from './chartCommon'

/* 积分消耗图 —— 按天累计 credits 的面积渐变折线（随天数与来源联动） */
const props = defineProps<{
  points: TrendPoint[] | null
  loading: boolean
}>()

const el = ref<HTMLElement | null>(null)
const hasDrawn = ref(false)

/** 按天累计值 */
const cum = computed(() => {
  let run = 0
  return (props.points ?? []).map((p) => (run += p.credits))
})
const isEmpty = computed(() => !(props.points ?? []).some((p) => p.credits > 0))

const { update } = useECharts(el, () => {
  const c = chartColors()
  const pts = props.points ?? []
  return {
    ...chartEntrance(!hasDrawn.value),
    grid: { left: 44, right: 16, top: 16, bottom: 26 },
    tooltip: {
      ...glassTooltip('axis'),
      axisPointer: { type: 'line' as const, lineStyle: { color: c.border } },
    },
    xAxis: {
      type: 'category' as const,
      boundaryGap: false,
      data: pts.map((p) => p.date.slice(5)),
      axisLine: { lineStyle: { color: c.border } },
      axisTick: { show: false },
      axisLabel: { color: c.textDim, fontSize: 11 },
    },
    yAxis: {
      type: 'value' as const,
      minInterval: 1,
      axisLabel: { color: c.textDim, fontSize: 11 },
      splitLine: { lineStyle: { color: c.border, type: 'dashed' as const } },
    },
    series: [
      {
        name: '累计积分',
        type: 'line' as const,
        data: cum.value,
        smooth: 0.35,
        showSymbol: false,
        symbol: 'circle',
        symbolSize: 6,
        lineStyle: { width: 2.5, color: c.accent },
        itemStyle: { color: c.accent },
        areaStyle: {
          color: {
            type: 'linear' as const,
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: withAlpha(c.accent, 0.28) },
              { offset: 1, color: withAlpha(c.accent, 0.02) },
            ],
          },
        },
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
  <GlassCard title="积分消耗" desc="按天累计积分（仅统计响应含 usage 的接口）">
    <Skeleton v-if="loading" height="260px" />
    <EmptyState
      v-else-if="isEmpty"
      icon="chart"
      title="暂无积分消耗"
      desc="所选范围内没有产生积分消耗的请求"
    />
    <div v-show="!loading && !isEmpty" ref="el" class="credits-chart"></div>
  </GlassCard>
</template>

<style scoped>
.credits-chart { width: 100%; height: 260px; }
</style>
