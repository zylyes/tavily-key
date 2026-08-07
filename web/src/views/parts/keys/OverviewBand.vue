<script setup lang="ts">
/* OverviewBand —— KeysView 首屏概览带：玻璃指标卡 + 异常警告条
   数据来自 getStats() 的 aggregate / recent_24h / anomalies（由父视图聚合后传入） */
import { computed } from 'vue'
import AnimatedNumber from '@/components/AnimatedNumber.vue'
import QuotaBar from '@/components/QuotaBar.vue'
import GIcon from '@/components/GIcon.vue'
import type { Aggregate } from '@/api/client'
import { fmtNum, fmtPct } from '@/utils/format'

const props = defineProps<{
  aggregate: Aggregate
  /** 近 24h 请求总数（recent_24h 聚合） */
  requests24h: number
  /** 近 24h 成功率（0-100）；无请求时为 null */
  successRate: number | null
  anomalyCount: number
}>()

const emit = defineEmits<{
  (e: 'show-anomalies'): void
}>()

const agg = computed(() => props.aggregate)
const hasLimit = computed(() => agg.value.total_limit > 0)
</script>

<template>
  <div class="ov-band">
    <div class="ov-grid stagger">
      <!-- 活跃 Key -->
      <div class="ov-card glass">
        <div class="ov-top">
          <span class="ov-icon"><GIcon name="key" :size="14" /></span>
          <span class="ov-label">活跃 Key</span>
        </div>
        <div class="ov-value">
          <AnimatedNumber :value="agg.active_keys" /><span class="ov-total u-dim"> / {{ fmtNum(agg.total_keys) }}</span>
        </div>
        <div class="ov-sub">
          <template v-if="agg.exhausted_count > 0">{{ agg.exhausted_count }} 个已耗尽</template>
          <template v-else>池内共 {{ fmtNum(agg.total_keys) }} 个</template>
        </div>
      </div>

      <!-- 剩余额度 -->
      <div class="ov-card glass">
        <div class="ov-top">
          <span class="ov-icon"><GIcon name="zap" :size="14" /></span>
          <span class="ov-label">剩余额度</span>
        </div>
        <div class="ov-value">
          <AnimatedNumber :value="Math.max(agg.remaining, 0)" :format="fmtNum" />
        </div>
        <div class="ov-quota">
          <QuotaBar
            v-if="hasLimit"
            :pct="agg.usage_pct"
            :used="agg.total_used"
            :limit="agg.total_limit"
            height="5px"
            :show-label="false"
          />
          <span class="ov-sub">{{ hasLimit ? `已用 ${fmtPct(agg.usage_pct)}` : '用量未同步' }}</span>
        </div>
      </div>

      <!-- 24h 请求数 -->
      <div class="ov-card glass">
        <div class="ov-top">
          <span class="ov-icon"><GIcon name="chart" :size="14" /></span>
          <span class="ov-label">24h 请求数</span>
        </div>
        <div class="ov-value"><AnimatedNumber :value="requests24h" :format="fmtNum" /></div>
        <div class="ov-sub">近 24 小时全部端点</div>
      </div>

      <!-- 成功率 -->
      <div class="ov-card glass">
        <div class="ov-top">
          <span class="ov-icon"><GIcon name="check" :size="14" /></span>
          <span class="ov-label">成功率</span>
        </div>
        <div class="ov-value">
          <AnimatedNumber v-if="successRate !== null" :value="successRate" :format="fmtPct" />
          <span v-else class="u-dim">—</span>
        </div>
        <div class="ov-sub">近 24 小时请求</div>
      </div>

      <!-- 异常数（>0 时可点击查看明细） -->
      <button
        type="button"
        class="ov-card glass ov-click"
        :class="{ 'has-anomaly': anomalyCount > 0 }"
        :disabled="anomalyCount === 0"
        @click="emit('show-anomalies')"
      >
        <div class="ov-top">
          <span class="ov-icon"><GIcon name="alert" :size="14" /></span>
          <span class="ov-label">异常 Key</span>
        </div>
        <div class="ov-value"><AnimatedNumber :value="anomalyCount" /></div>
        <div class="ov-sub">{{ anomalyCount > 0 ? '点击查看明细' : '运行正常' }}</div>
      </button>
    </div>

    <!-- 异常警告条 -->
    <button v-if="anomalyCount > 0" type="button" class="anomaly-bar stagger-item" style="--i: 6" @click="emit('show-anomalies')">
      <GIcon name="alert" :size="14" class="ab-icon" />
      <span class="ab-text">
        检测到 <b class="u-num">{{ anomalyCount }}</b> 个 Key 存在异常（耗尽 / 疑似泄露 / 高错误率等），建议及时处理
      </span>
      <span class="u-grow"></span>
      <span class="ab-more">查看明细<GIcon name="chevronRight" :size="12" /></span>
    </button>
  </div>
</template>

<style scoped>
.ov-band { display: flex; flex-direction: column; gap: 10px; }

.ov-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}

.ov-card {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 13px 14px 12px;
  border-radius: var(--r-card);
  text-align: left;
  min-width: 0;
}

.ov-top { display: flex; align-items: center; gap: 7px; }
.ov-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: var(--r-sm);
  background: var(--accent-softer);
  color: var(--accent-text);
  flex: none;
}
.ov-label { font-size: 11px; color: var(--text-3); white-space: nowrap; }

.ov-value {
  font-size: 21px;
  font-weight: 650;
  letter-spacing: -0.01em;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}
.ov-total { font-size: 12.5px; font-weight: 500; }

.ov-sub { font-size: 11px; color: var(--text-3); white-space: nowrap; }
.ov-quota { display: flex; flex-direction: column; gap: 5px; }

/* 可点击的异常卡 */
.ov-click { cursor: pointer; transition: border-color var(--dur-2) var(--ease-out), transform var(--dur-2) var(--ease-out); }
.ov-click:disabled { cursor: default; }
.ov-click:not(:disabled):hover { border-color: var(--glass-border-strong); transform: translateY(-2px); }
.ov-card.has-anomaly .ov-icon { background: var(--warn-soft); color: var(--warn); }
.ov-card.has-anomaly .ov-value { color: var(--warn); }

/* ── 异常警告条 ─────────────────────────────────────────── */
.anomaly-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 13px;
  border-radius: var(--r-ctrl);
  background: var(--warn-soft);
  border: 1px solid color-mix(in srgb, var(--warn) 32%, transparent);
  color: var(--text);
  font-size: 12px;
  text-align: left;
  transition: background var(--dur-1) ease, border-color var(--dur-1) ease;
}
.anomaly-bar:hover { border-color: color-mix(in srgb, var(--warn) 55%, transparent); }
.ab-icon { color: var(--warn); flex: none; }
.ab-text b { color: var(--warn); }
.ab-more {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex: none;
  font-size: 11.5px;
  color: var(--warn);
  font-weight: 550;
}
</style>
