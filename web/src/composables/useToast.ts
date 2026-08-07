import { ref, type Ref } from 'vue'

/* ═══════════════════════════════════════════════════════════════
   useToast —— 全局轻提示（模块级单例）
   const toast = useToast(); toast.success('已保存')
   宿主组件 <GToast /> 已在 App.vue 挂好，视图直接用 composable 即可。
   ═══════════════════════════════════════════════════════════════ */

export type ToastType = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  type: ToastType
  text: string
  duration: number
}

const toasts = ref<ToastItem[]>([])
let seq = 0

const DEFAULT_DURATION: Record<ToastType, number> = {
  success: 2600,
  info: 3200,
  error: 4200,
}

function dismiss(id: number): void {
  toasts.value = toasts.value.filter((t) => t.id !== id)
}

function push(type: ToastType, text: string, duration?: number): number {
  const id = ++seq
  const dur = duration ?? DEFAULT_DURATION[type]
  toasts.value.push({ id, type, text, duration: dur })
  if (toasts.value.length > 5) toasts.value.shift()   // 最多堆 5 条
  if (dur > 0) setTimeout(() => dismiss(id), dur)
  return id
}

export interface ToastApi {
  toasts: Ref<ToastItem[]>
  success: (text: string, duration?: number) => number
  error: (text: string, duration?: number) => number
  info: (text: string, duration?: number) => number
  dismiss: (id: number) => void
}

export function useToast(): ToastApi {
  return {
    toasts,
    success: (text, duration) => push('success', text, duration),
    error: (text, duration) => push('error', text, duration),
    info: (text, duration) => push('info', text, duration),
    dismiss,
  }
}
