<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import GlassCard from '@/components/GlassCard.vue'
import GButton from '@/components/GButton.vue'
import GIcon from '@/components/GIcon.vue'
import GSelect from '@/components/GSelect.vue'
import Skeleton from '@/components/Skeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import { usePolling } from '@/composables/usePolling'
import { getStats, getUsageTrend } from '@/api/client'
import { fmtNum } from '@/utils/format'
import StatCard from './parts/stats/StatCard.vue'
import SegmentedControl from './parts/stats/SegmentedControl.vue'
import TrendChart from './parts/stats/TrendChart.vue'
import EndpointChart from './parts/stats/EndpointChart.vue'
import CreditsChart from './parts/stats/CreditsChart.vue'

/* ═══ 筛选状态：天数与来源对本页所有图表联动 ═══ */
const days = ref(7)
const source = ref('')

const dayOptions = [
  { label: '近 7 天', value: 7 },
  { label: '近 14 天', value: 14 },
  { label: '近 30 天', value: 30 },
]
const sourceOptions = [
  { label: '全部来源', value: '' },
  { label: 'MCP', value: 'mcp' },
  { label: '搜索代理', value: 'proxy' },
  { label: 'CLI', value: 'cli' },
]

/* ═══ 数据：stats（指标卡）+ trend（全部图表），12s 静默轮询 ═══ */
const {
  data: stats,
  loading: statsLoading,
  refreshing: statsRefreshing,
  error: statsError,
  refresh: refreshStats,
} = usePolling(getStats, { interval: 12000 })

const {
  data: trendResp,
  loading: trendLoading,
  refreshing: trendRefreshing,
  error: trendError,
  refresh: refreshTrend,
} = usePolling(() => getUsageTrend(days.value, source.value), { interval: 12000 })

// 筛选变化 → 立即重取趋势（闭包读取最新 days/source）
watch([days, source], () => {
  void refreshTrend()
})

const points = computed(() => trendResp.value?.trend.points ?? null)

/* ═══ 指标卡派生数据 ═══ */
const recent24 = computed(() => {
  const r = stats.value?.recent_24h ?? {}
  let success = 0
  let failed = 0
  for (const v of Object.values(r)) {
    success += v.success
    failed += v.failed
  }
  const total = success + failed
  return { success, failed, total, rate: total ? (success / total) * 100 : 0 }
})

const rateTone = computed<'success' | 'info' | 'warn'>(() => {
  if (!recent24.value.total) return 'info'
  const r = recent24.value.rate
  return r >= 95 ? 'success' : r >= 80 ? 'info' : 'warn'
})

const refreshing = computed(() => statsRefreshing.value || trendRefreshing.value)

async function refreshAll(): Promise<void> {
  // 手动刷新：跳过 inFlight 防重入，busy 至少 300ms 给按钮可感知的反馈
  await Promise.all([
    refreshStats({ force: true, minBusyMs: 300 }),
    refreshTrend({ force: true, minBusyMs: 300 }),
  ])
}

/** 首载双失败才显示错误块（已有数据时静默等待下一轮） */
const loadError = computed(() => {
  if (stats.value || trendResp.value) return null
  return statsError.value ?? trendError.value
})
</script>

<template>
  <div class="view">
    <PageHeader title="用量统计" desc="Key 池容量、请求趋势与积分消耗全景">
      <template #actions>
        <GSelect v-model="source" :options="sourceOptions" size="sm" />
        <SegmentedControl v-model="days" :options="dayOptions" />
        <GButton size="sm" :busy="refreshing" @click="refreshAll">
          <GIcon name="refresh" :size="13" />刷新
        </GButton>
      </template>
    </PageHeader>

    <GlassCard v-if="loadError">
      <EmptyState icon="alert" title="统计数据加载失败" :desc="loadError.message">
        <GButton variant="primary" size="sm" @click="refreshAll">重试</GButton>
      </EmptyState>
    </GlassCard>

    <div v-else class="view-body stagger">
      <!-- 关键指标卡 -->
      <div v-if="statsLoading" class="stat-grid">
        <GlassCard v-for="i in 4" :key="i">
          <Skeleton width="42%" />
          <Skeleton height="26px" width="58%" style="margin-top: 14px" />
          <Skeleton width="72%" style="margin-top: 12px" />
        </GlassCard>
      </div>
      <div v-else class="stat-grid">
        <StatCard
          label="总 Key"
          icon="key"
          tone="accent"
          :value="stats?.total_keys ?? 0"
          :sub="`活跃 ${fmtNum(stats?.active_keys ?? 0)} · 耗尽 ${fmtNum(stats?.aggregate.exhausted_count ?? 0)}`"
        />
        <StatCard
          label="24h 请求"
          icon="zap"
          tone="info"
          :value="recent24.total"
          :sub="`成功 ${fmtNum(recent24.success)} · 失败 ${fmtNum(recent24.failed)}`"
        />
        <StatCard
          label="24h 成功率"
          icon="shield"
          :tone="rateTone"
          :value="recent24.rate"
          :decimals="1"
          suffix="%"
          :sub="recent24.total ? `基于 ${fmtNum(recent24.total)} 次请求` : '近 24h 暂无请求'"
        />
        <StatCard
          label="总积分消耗"
          icon="chart"
          tone="warn"
          :value="stats?.total_credits ?? 0"
          :sub="`剩余 ${fmtNum(stats?.aggregate.remaining ?? 0)} 积分`"
        />
      </div>

      <!-- 请求趋势（全宽） -->
      <TrendChart :points="points" :loading="trendLoading" :days="days" />

      <!-- 接口分布 + 积分消耗（窄屏单列堆叠） -->
      <div class="chart-grid">
        <EndpointChart :points="points" :loading="trendLoading" :days="days" />
        <CreditsChart :points="points" :loading="trendLoading" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.view-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
}

.chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 14px;
  align-items: start;
}
</style>
