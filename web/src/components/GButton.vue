<script setup lang="ts">
/* GButton —— 按钮
   用法：
     <GButton variant="primary" :busy="saving" @click="save">保存</GButton>
     <GButton size="sm" variant="danger">删除</GButton> */
const props = withDefaults(defineProps<{
  variant?: 'primary' | 'ghost' | 'danger' | 'warn'
  size?: 'sm' | 'md'
  busy?: boolean
  disabled?: boolean
  type?: 'button' | 'submit'
  /** 无边框纯图标/文字按钮（表格行内操作等） */
  text?: boolean
}>(), {
  variant: 'ghost',
  size: 'md',
  busy: false,
  disabled: false,
  type: 'button',
  text: false,
})

const emit = defineEmits<{
  (e: 'click', ev: MouseEvent): void
}>()

function onClick(ev: MouseEvent): void {
  if (props.busy || props.disabled) {
    ev.preventDefault()
    ev.stopPropagation()
    return
  }
  emit('click', ev)
}
</script>

<template>
  <button
    class="g-btn"
    :class="[`v-${variant}`, `s-${size}`, { busy, text }]"
    :type="type"
    :disabled="disabled || busy"
    :aria-busy="busy || undefined"
    @click="onClick"
  >
    <svg v-if="busy" class="spin g-btn-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-opacity=".25" stroke-width="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" stroke-width="3" stroke-linecap="round" />
    </svg>
    <slot />
  </button>
</template>

<style scoped>
.g-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: var(--r-ctrl);
  font-weight: 550;
  white-space: nowrap;
  border: 1px solid transparent;
  transition: transform var(--dur-1) var(--ease-out), box-shadow var(--dur-1) var(--ease-out),
    background var(--dur-1) ease, border-color var(--dur-1) ease, opacity var(--dur-1) ease,
    color var(--dur-1) ease;
  will-change: transform;
}
.g-btn:active:not(:disabled) { transform: translateY(1px); }
.g-btn:disabled { opacity: .55; cursor: not-allowed; }

.s-md { height: 32px; padding: 0 14px; font-size: 12.5px; }
.s-sm { height: 26px; padding: 0 10px; font-size: 11.5px; border-radius: var(--r-sm); }

.g-btn-spinner { margin: -2px 0; }

/* busy 且槽内有前导图标：spinner 占据图标的固定位置（图标保留占位但隐藏），
   使按钮宽度与文字位置 busy 前后不变；无图标 busy 按钮 spinner 自然前置于文字 */
.g-btn.busy > :deep(.g-icon) {
  visibility: hidden;
  /* -(spinner 宽 14px + flex gap 6px)：把图标拉回与 spinner 重叠的原位 */
  margin-left: -20px;
}

/* primary：靛蓝紫渐变 */
.v-primary {
  background: var(--accent-grad);
  color: var(--on-accent);
  box-shadow: 0 4px 14px -4px var(--accent-soft), inset 0 1px 0 rgba(255, 255, 255, .18);
}
.v-primary:hover:not(:disabled) { filter: brightness(1.08); }

/* ghost：玻璃描边 */
.v-ghost {
  background: var(--glass-bg);
  border-color: var(--glass-border);
  color: var(--text);
}
.v-ghost:hover:not(:disabled) {
  background: var(--glass-bg-2);
  border-color: var(--glass-border-strong);
}

/* danger / warn */
.v-danger {
  background: var(--danger-soft);
  border-color: color-mix(in srgb, var(--danger) 35%, transparent);
  color: var(--danger);
}
.v-danger:hover:not(:disabled) { background: color-mix(in srgb, var(--danger) 22%, transparent); }
.v-warn {
  background: var(--warn-soft);
  border-color: color-mix(in srgb, var(--warn) 35%, transparent);
  color: var(--warn);
}
.v-warn:hover:not(:disabled) { background: color-mix(in srgb, var(--warn) 22%, transparent); }

/* text：纯文字按钮 */
.g-btn.text {
  background: none;
  border-color: transparent;
  color: var(--accent-text);
  padding: 0 8px;
}
.g-btn.text:hover:not(:disabled) { background: var(--accent-softer); }
.g-btn.text.v-danger { color: var(--danger); }
.g-btn.text.v-danger:hover:not(:disabled) { background: var(--danger-soft); }
.g-btn.text.v-warn { color: var(--warn); }
.g-btn.text.v-warn:hover:not(:disabled) { background: var(--warn-soft); }
</style>
