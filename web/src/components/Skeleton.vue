<script setup lang="ts">
/* Skeleton —— 呼吸光泽骨架屏
   用法：<Skeleton :lines="3" /> <Skeleton height="120px" /> <Skeleton circle width="36px" /> */
withDefaults(defineProps<{
  lines?: number
  height?: string
  width?: string
  circle?: boolean
}>(), {
  lines: 1,
  height: '14px',
  width: '100%',
  circle: false,
})
</script>

<template>
  <div class="skeleton" aria-hidden="true">
    <div
      v-for="i in lines"
      :key="i"
      class="skeleton-bar"
      :class="{ circle }"
      :style="{
        height: circle ? width : height,
        width,
        borderRadius: circle ? '50%' : undefined,
        marginBottom: i < lines && !circle ? '10px' : '0',
        // 多行时末行收窄，更像真实文本
        maxWidth: lines > 1 && i === lines && !circle ? '72%' : undefined,
      }"
    />
  </div>
</template>

<style scoped>
.skeleton-bar {
  background: linear-gradient(
    90deg,
    var(--neutral-soft) 25%,
    var(--glass-border-strong) 50%,
    var(--neutral-soft) 75%
  );
  background-size: 200% 100%;
  border-radius: var(--r-sm);
  animation: shimmer 1.4s ease-in-out infinite;
}
</style>
