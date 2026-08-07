<script setup lang="ts">
/* GBadge —— 柔光胶囊徽章
   用法：<GBadge type="success">运行中</GBadge> <GBadge type="fail" dot>已停用</GBadge> */
withDefaults(defineProps<{
  type?: 'success' | 'fail' | 'warn' | 'info' | 'neutral'
  dot?: boolean
}>(), {
  type: 'neutral',
  dot: false,
})
</script>

<template>
  <span class="g-badge" :class="`t-${type}`">
    <i v-if="dot" class="g-badge-dot" aria-hidden="true" />
    <slot />
  </span>
</template>

<style scoped>
.g-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 21px;
  padding: 0 9px;
  border-radius: var(--r-pill);
  font-size: 11px;
  font-weight: 550;
  line-height: 1;
  white-space: nowrap;
  border: 1px solid transparent;
}
.g-badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}

.t-success { color: var(--success); background: var(--success-soft); border-color: color-mix(in srgb, var(--success) 28%, transparent); }
.t-fail { color: var(--danger); background: var(--danger-soft); border-color: color-mix(in srgb, var(--danger) 28%, transparent); }
.t-warn { color: var(--warn); background: var(--warn-soft); border-color: color-mix(in srgb, var(--warn) 28%, transparent); }
.t-info { color: var(--info); background: var(--info-soft); border-color: color-mix(in srgb, var(--info) 28%, transparent); }
.t-neutral { color: var(--text-2); background: var(--neutral-soft); border-color: var(--glass-border); }
</style>
