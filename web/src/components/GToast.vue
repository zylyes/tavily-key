<script setup lang="ts">
import GIcon from './GIcon.vue'
import { useToast, type ToastType } from '@/composables/useToast'

/* GToast —— Toast 宿主（App.vue 挂一次；视图用 useToast() 发消息） */
const { toasts, dismiss } = useToast()

const ICON_MAP: Record<ToastType, string> = {
  success: 'check',
  error: 'alert',
  info: 'info',
}
</script>

<template>
  <div class="toast-wrap" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="t in toasts"
        :key="t.id"
        class="toast"
        :class="`t-${t.type}`"
        role="status"
        @click="dismiss(t.id)"
      >
        <GIcon :name="ICON_MAP[t.type]" :size="14" class="toast-icon" />
        <span class="toast-text">{{ t.text }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-wrap {
  position: fixed;
  right: 18px;
  bottom: 18px;
  z-index: var(--z-toast);
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-end;
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  max-width: 360px;
  padding: 10px 14px;
  border-radius: var(--r-ctrl);
  font-size: 12px;
  line-height: 1.5;
  cursor: pointer;
  background:
    linear-gradient(180deg, var(--glass-hi) 0%, transparent 60px),
    var(--glass-bg-2);
  backdrop-filter: blur(24px) saturate(1.6);
  -webkit-backdrop-filter: blur(24px) saturate(1.6);
  border: 1px solid var(--glass-border-strong);
  box-shadow: var(--shadow-pop);
  color: var(--text);
}
.toast-icon { flex: none; margin-top: 1px; }
.toast-text { white-space: pre-line; word-break: break-all; }

.t-success .toast-icon { color: var(--success); }
.t-error .toast-icon { color: var(--danger); }
.t-info .toast-icon { color: var(--info); }
</style>
