<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import GIcon from './GIcon.vue'

/* GModal —— 玻璃模态（弹簧入场 / Esc / 点遮罩关闭 / 焦点圈定）
   用法：
     const open = ref(false)
     <GModal v-model:open="open" title="添加 Key" width="520px">
       内容
       <template #footer>
         <GButton @click="open = false">取消</GButton>
         <GButton variant="primary" @click="submit">确定</GButton>
       </template>
     </GModal> */
const props = withDefaults(defineProps<{
  open: boolean
  title?: string
  width?: string
  /** 是否允许 Esc / 点遮罩关闭（登录模态传 false） */
  closable?: boolean
}>(), {
  width: '460px',
  closable: true,
})

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'close'): void
}>()

const dialogRef = ref<HTMLElement | null>(null)
let lastFocused: HTMLElement | null = null

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function close(): void {
  if (!props.closable) return
  emit('update:open', false)
  emit('close')
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && props.closable) {
    e.stopPropagation()
    close()
    return
  }
  // 焦点圈定：Tab 在对话框内循环
  if (e.key === 'Tab' && dialogRef.value) {
    const els = Array.from(dialogRef.value.querySelectorAll<HTMLElement>(FOCUSABLE))
      .filter((el) => el.offsetParent !== null)
    if (!els.length) return
    const first = els[0]
    const last = els[els.length - 1]
    const active = document.activeElement as HTMLElement | null
    if (e.shiftKey && (active === first || !dialogRef.value.contains(active))) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && active === last) {
      e.preventDefault()
      first.focus()
    }
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      lastFocused = document.activeElement as HTMLElement | null
      window.addEventListener('keydown', onKeydown, true)
      await nextTick()
      const el = dialogRef.value?.querySelector<HTMLElement>(FOCUSABLE)
      el?.focus()
    } else {
      window.removeEventListener('keydown', onKeydown, true)
      lastFocused?.focus?.()
    }
  },
)
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="open"
        class="gmodal-mask"
        role="presentation"
        @mousedown.self="close"
      >
        <div
          ref="dialogRef"
          class="gmodal-dialog"
          :style="{ width, maxWidth: 'calc(100vw - 40px)' }"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
        >
          <header v-if="title || closable" class="gmodal-head">
            <h3 class="gmodal-title">{{ title }}</h3>
            <button v-if="closable" class="gmodal-x" aria-label="关闭" @click="close">
              <GIcon name="x" :size="14" />
            </button>
          </header>
          <div class="gmodal-body">
            <slot />
          </div>
          <footer v-if="$slots.footer" class="gmodal-foot">
            <slot name="footer" />
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.gmodal-mask {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--mask);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  padding: 20px;
}
.gmodal-dialog {
  background:
    linear-gradient(180deg, var(--glass-hi) 0%, transparent 120px),
    var(--glass-bg-2);
  backdrop-filter: blur(28px) saturate(1.6);
  -webkit-backdrop-filter: blur(28px) saturate(1.6);
  border: 1px solid var(--glass-border-strong);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
  max-height: calc(100vh - 60px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.gmodal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px 0;
}
.gmodal-title { font-size: 14px; }
.gmodal-x {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--r-sm);
  color: var(--text-3);
  transition: background var(--dur-1) ease, color var(--dur-1) ease;
}
.gmodal-x:hover { background: var(--neutral-soft); color: var(--text); }
.gmodal-body {
  padding: 14px 18px 18px;
  overflow-y: auto;
}
.gmodal-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 0 18px 16px;
}
</style>
