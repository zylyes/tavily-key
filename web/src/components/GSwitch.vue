<script setup lang="ts">
/* GSwitch —— 开关（v-model）
   用法：<GSwitch v-model="enabled" @change="onToggle" /> */
const props = withDefaults(defineProps<{
  modelValue: boolean
  disabled?: boolean
  label?: string
}>(), {
  disabled: false,
})

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'change', v: boolean): void
}>()

function toggle(): void {
  if (props.disabled) return
  const v = !props.modelValue
  emit('update:modelValue', v)
  emit('change', v)
}
</script>

<template>
  <button
    type="button"
    class="g-switch"
    :class="{ on: modelValue, labeled: !!label }"
    role="switch"
    :aria-checked="modelValue"
    :aria-label="label"
    :disabled="disabled"
    @click="toggle"
  >
    <span v-if="label" class="g-switch-label">{{ label }}</span>
    <span class="g-switch-track"><span class="g-switch-thumb" /></span>
  </button>
</template>

<style scoped>
.g-switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border-radius: var(--r-pill);
}
.g-switch:disabled { opacity: .5; cursor: not-allowed; }
.g-switch-label { font-size: 12px; color: var(--text-2); }

.g-switch-track {
  position: relative;
  width: 36px;
  height: 20px;
  border-radius: var(--r-pill);
  background: var(--neutral-soft);
  border: 1px solid var(--glass-border);
  transition: background var(--dur-2) var(--ease-out), border-color var(--dur-2) var(--ease-out);
}
.on .g-switch-track {
  background: var(--accent-grad);
  border-color: transparent;
}
.g-switch-thumb {
  position: absolute;
  top: 50%;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, .35);
  transform: translateY(-50%);
  transition: left var(--dur-2) var(--ease-spring);
}
.on .g-switch-thumb { left: 18px; }
</style>
