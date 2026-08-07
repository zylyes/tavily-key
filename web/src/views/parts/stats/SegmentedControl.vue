<script setup lang="ts">
/* SegmentedControl —— 分段选择器（统计页时间范围切换，与 sm 控件同高 26px） */
defineProps<{
  modelValue: string | number
  options: Array<{ label: string; value: string | number }>
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: string | number): void
}>()
</script>

<template>
  <div class="seg" role="tablist">
    <button
      v-for="opt in options"
      :key="String(opt.value)"
      type="button"
      class="seg-item"
      :class="{ active: opt.value === modelValue }"
      role="tab"
      :aria-selected="opt.value === modelValue"
      @click="emit('update:modelValue', opt.value)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>

<style scoped>
.seg {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: var(--r-sm);
  background: var(--input-bg);
  border: 1px solid var(--glass-border);
}
.seg-item {
  height: 20px;
  padding: 0 10px;
  border-radius: calc(var(--r-sm) - 3px);
  font-size: 11.5px;
  font-weight: 550;
  color: var(--text-3);
  white-space: nowrap;
  transition:
    color var(--dur-1) ease,
    background var(--dur-1) ease,
    box-shadow var(--dur-1) ease;
}
.seg-item:hover:not(.active) { color: var(--text-2); }
.seg-item.active {
  color: var(--accent-text);
  background: var(--accent-grad-soft);
  box-shadow: inset 0 0 0 1px var(--accent-soft);
}
</style>
