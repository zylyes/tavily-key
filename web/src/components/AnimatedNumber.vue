<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

/* AnimatedNumber —— 数字滚动计数
   用法：
     <AnimatedNumber :value="stats.total_requests" />
     <AnimatedNumber :value="agg.remaining" :duration="800" :format="fmtNum" suffix=" 次" /> */
const props = withDefaults(defineProps<{
  value: number
  /** 动画时长 ms（默认 600） */
  duration?: number
  /** 保留小数位（format 未传时生效，默认 0） */
  decimals?: number
  /** 自定义格式化（优先于 decimals/prefix/suffix） */
  format?: (n: number) => string
  prefix?: string
  suffix?: string
}>(), {
  duration: 600,
  decimals: 0,
})

const display = ref(props.value)
let raf = 0

function reducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

function render(n: number): string {
  if (props.format) return props.format(n)
  const fixed = props.decimals > 0 ? n.toFixed(props.decimals) : Math.round(n).toLocaleString('en-US')
  return `${props.prefix ?? ''}${fixed}${props.suffix ?? ''}`
}

watch(
  () => props.value,
  (to, from) => {
    cancelAnimationFrame(raf)
    const start = typeof from === 'number' && Number.isFinite(from) ? from : to
    if (reducedMotion() || start === to || props.duration <= 0) {
      display.value = to
      return
    }
    const t0 = performance.now()
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / props.duration)
      const eased = 1 - Math.pow(1 - p, 3)   // easeOutCubic
      display.value = start + (to - start) * eased
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
  },
)

onBeforeUnmount(() => cancelAnimationFrame(raf))
</script>

<template>
  <span class="animated-number u-num">{{ render(display) }}</span>
</template>

<style scoped>
.animated-number { display: inline-block; }
</style>
