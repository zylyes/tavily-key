<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import GlassCard from '@/components/GlassCard.vue'
import Skeleton from '@/components/Skeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useECharts, chartColors } from '@/composables/useECharts'
import { fmtNum } from '@/utils/format'
import type { TrendPoint } from '@/api/client'
import { chartEntrance, glassTooltip, withAlpha } from './chartCommon'

/* 请求趋势图 —— 按天柱状堆叠（成功绿 / 失败红），随天数与来源联动 */
const props = defineProps<{
  points: TrendPoint[] | null
  loading: boolean
  days: number
}>()

const el = ref<HTMLElement | null>(null)
/** 首绘完成后关闭入场动画（静默轮询/主题切换不重放） */
const hasDrawn = ref(false)

const isEmpty = computed(
  () => !props.points?.length || props.points.every((p) => p.requests === 0),
)

const summary = computed(() => {
  const pts = props.points ?? []
  const req = pts.reduce((s, p) => s + p.requests, 0)
  const ok = pts.reduce((s, p) => s + p.success, 0)
  const credits = pts.reduce((s, p) => s + p.credits, 0)
  return `近 ${props.days} 天 · 请求 ${fmtNum(req)} · 成功 ${fmtNum(ok)} · 积分 ${fmtNum(credits)}`
})

const { update } = useECharts(el, () => {
  const c = chartColors()
  const pts = props.points ?? []
  const topRadius: number[] = [3, 3, 0, 0]
  return {
    ...chartEntrance(!hasDrawn.value),
    grid: { left: 44, right: 12, top: 30, bottom: 26 },
    legend: {
      top: 0,
      right: 0,
      icon: 'roundRect',
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 14,
      textStyle: { color: c.textDim, fontSize: 11 },
    },
    tooltip: {
      ...glassTooltip('axis'),
      axisPointer: { type: 'shadow' as const, shadowStyle: { color: withAlpha(c.accent, 0.07) } },
    },
    xAxis: {
      type: 'category' as const,
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
        name: '成功',
        type: 'bar' as const,
        stack: 'req',
        barMaxWidth: 26,
        itemStyle: { color: c.success },
        // 堆叠在底层：当日无失败时给成功柱补圆角，视觉更完整
        data: pts.map((p) => ({
          value: p.success,
          itemStyle: { borderRadius: p.failed > 0 ? 0 : topRadius },
        })),
      },
      {
        name: '失败',
        type: 'bar' as const,
        stack: 'req',
        itemStyle: { color: c.danger, borderRadius: topRadius },
        data: pts.map((p) => p.failed),
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
  <GlassCard title="请求趋势" desc="按天聚合请求数（绿 = 成功 / 红 = 失败），悬停柱子查看明细">
    <template #actions>
      <span v-if="!loading && !isEmpty" class="trend-summary u-num u-dim">{{ summary }}</span>
    </template>
    <Skeleton v-if="loading" height="280px" />
    <EmptyState
      v-else-if="isEmpty"
      icon="chart"
      title="暂无请求数据"
      desc="所选时间范围与来源内没有请求记录"
    />
    <div v-show="!loading && !isEmpty" ref="el" class="trend-chart"></div>
  </GlassCard>
</template>

<style scoped>
.trend-summary { align-self: center; font-size: 11px; }
.trend-chart { width: 100%; height: 280px; }
</style>
