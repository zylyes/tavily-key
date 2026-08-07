<script setup lang="ts">
import { computed } from 'vue'
import { fmtNum } from '@/utils/format'

/* QuotaBar —— 额度条（按用量绿→琥珀→红渐变，动画宽度）
   用法：
     <QuotaBar :pct="key.usage_pct" :used="key.credits_used" :limit="key.credits_limit" />
     <QuotaBar :pct="75" :show-label="false" height="6px" /> */
const props = withDefaults(defineProps<{
  /** 用量百分比 0-100（超出自动截断） */
  pct: number
  /** 已用值（用于标签，可选） */
  used?: number
  /** 上限值（用于标签，可选；0/缺省表示无限额） */
  limit?: number
  height?: string
  showLabel?: boolean
}>(), {
  height: '8px',
  showLabel: true,
})

const clamped = computed(() => Math.max(0, Math.min(100, props.pct || 0)))

const level = computed<'ok' | 'warn' | 'danger'>(() => {
  if (clamped.value >= 90) return 'danger'
  if (clamped.value >= 70) return 'warn'
  return 'ok'
})
</script>

<template>
  <div class="quota" :class="`lv-${level}`">
    <div
      class="quota-track"
      :style="{ height }"
      role="progressbar"
      :aria-valuenow="Math.round(clamped)"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div class="quota-fill" :style="{ width: `${clamped}%` }" />
    </div>
    <div v-if="showLabel" class="quota-label u-num">
      <template v-if="used !== undefined && limit !== undefined && limit > 0">
        {{ fmtNum(used) }} / {{ fmtNum(limit) }}（{{ clamped.toFixed(1) }}%）
      </template>
      <template v-else-if="used !== undefined && !limit">
        {{ fmtNum(used) }} / 无限额
      </template>
      <template v-else>{{ clamped.toFixed(1) }}%</template>
    </div>
  </div>
</template>

<style scoped>
.quota { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.quota-track {
  width: 100%;
  border-radius: var(--r-pill);
  background: var(--neutral-soft);
  border: 1px solid var(--glass-border);
  overflow: hidden;
}
.quota-fill {
  height: 100%;
  border-radius: var(--r-pill);
  transition: width var(--dur-3) var(--ease-out);
}
.lv-ok .quota-fill { background: linear-gradient(90deg, var(--success), color-mix(in srgb, var(--success) 70%, var(--info))); }
.lv-warn .quota-fill { background: linear-gradient(90deg, var(--warn), color-mix(in srgb, var(--warn) 75%, var(--danger))); }
.lv-danger .quota-fill { background: linear-gradient(90deg, var(--danger), color-mix(in srgb, var(--danger) 80%, #ff9d9d)); }
.quota-label {
  font-size: 11px;
  color: var(--text-3);
  white-space: nowrap;
}
</style>
