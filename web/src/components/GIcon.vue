<script setup lang="ts">
import { ICONS } from '@/icons'

/* GIcon —— 内联 SVG 线性图标（见 @/icons 的 ICONS 表） */
withDefaults(defineProps<{
  name: string
  size?: number | string
  strokeWidth?: number
}>(), {
  size: 16,
  strokeWidth: 1.8,
})
</script>

<template>
  <svg
    class="g-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    v-html="ICONS[name] || ''"
  />
</template>

<style scoped>
.g-icon {
  display: inline-block;
  vertical-align: middle;
  flex: none;
}
</style>
