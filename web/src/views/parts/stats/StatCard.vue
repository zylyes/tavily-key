<script setup lang="ts">
import AnimatedNumber from '@/components/AnimatedNumber.vue'
import GIcon from '@/components/GIcon.vue'
import GlassCard from '@/components/GlassCard.vue'

/* StatCard —— 关键指标卡（图标 chip + 滚动数字 + 弱提示行） */
withDefaults(defineProps<{
  label: string
  value: number
  icon: string
  /** 图标 chip 色调 */
  tone?: 'accent' | 'success' | 'info' | 'warn' | 'danger'
  /** 底部弱提示行 */
  sub?: string
  decimals?: number
  suffix?: string
  format?: (n: number) => string
}>(), {
  tone: 'accent',
  decimals: 0,
})
</script>

<template>
  <GlassCard hover class="stat-card">
    <div class="sc-head">
      <span class="sc-chip" :class="`t-${tone}`"><GIcon :name="icon" :size="14" /></span>
      <span class="sc-label u-ellipsis">{{ label }}</span>
    </div>
    <div class="sc-value u-num">
      <AnimatedNumber
        :value="value"
        :format="format"
        :decimals="decimals"
        :suffix="suffix"
        :duration="700"
      />
    </div>
    <p v-if="sub" class="sc-sub u-ellipsis" :title="sub">{{ sub }}</p>
  </GlassCard>
</template>

<style scoped>
.stat-card { min-width: 0; }

.sc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.sc-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--r-sm);
  flex: none;
}
.t-accent { color: var(--accent-text); background: var(--accent-soft); }
.t-success { color: var(--success); background: var(--success-soft); }
.t-info { color: var(--info); background: var(--info-soft); }
.t-warn { color: var(--warn); background: var(--warn-soft); }
.t-danger { color: var(--danger); background: var(--danger-soft); }

.sc-label {
  font-size: 12px;
  font-weight: 550;
  color: var(--text-2);
}
.sc-value {
  font-size: 24px;
  font-weight: 680;
  line-height: 1.15;
  letter-spacing: 0.01em;
  color: var(--text);
}
.sc-sub {
  margin-top: 5px;
  font-size: 11px;
  color: var(--text-3);
}
</style>
