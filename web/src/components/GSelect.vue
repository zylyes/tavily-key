<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import GIcon from './GIcon.vue'

/* GSelect —— 自定义玻璃下拉（v-model）
   用法：
     <GSelect v-model="status" :options="[
       { label: '全部', value: '' },
       { label: '成功', value: 'success' },
     ]" placeholder="状态" /> */
export interface GSelectOption {
  label: string
  value: string | number
  hint?: string
}

const props = withDefaults(defineProps<{
  modelValue: string | number | null
  options: GSelectOption[]
  placeholder?: string
  disabled?: boolean
  size?: 'sm' | 'md'
}>(), {
  placeholder: '请选择',
  size: 'md',
})

const emit = defineEmits<{
  (e: 'update:modelValue', v: string | number): void
  (e: 'change', v: string | number): void
}>()

const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)

const selected = computed(() =>
  props.options.find((o) => o.value === props.modelValue) ?? null)

function toggle(): void {
  if (props.disabled) return
  open.value = !open.value
}

function pick(opt: GSelectOption): void {
  emit('update:modelValue', opt.value)
  emit('change', opt.value)
  open.value = false
}

function onDocMousedown(ev: MouseEvent): void {
  if (rootRef.value && !rootRef.value.contains(ev.target as Node)) open.value = false
}
function onKeydown(ev: KeyboardEvent): void {
  if (ev.key === 'Escape') open.value = false
}

watch(open, (v) => {
  if (v) {
    document.addEventListener('mousedown', onDocMousedown, true)
    document.addEventListener('keydown', onKeydown, true)
  } else {
    document.removeEventListener('mousedown', onDocMousedown, true)
    document.removeEventListener('keydown', onKeydown, true)
  }
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocMousedown, true)
  document.removeEventListener('keydown', onKeydown, true)
})
</script>

<template>
  <div ref="rootRef" class="g-select" :class="[`s-${size}`, { open, disabled }]">
    <button
      type="button"
      class="g-select-trigger"
      :disabled="disabled"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="toggle"
    >
      <span class="g-select-label u-ellipsis" :class="{ placeholder: !selected }">
        {{ selected ? selected.label : placeholder }}
      </span>
      <GIcon name="chevronDown" :size="13" class="g-select-arrow" />
    </button>
    <Transition name="drop">
      <div v-if="open" class="g-select-pop" role="listbox">
        <button
          v-for="opt in options"
          :key="String(opt.value)"
          type="button"
          class="g-select-opt"
          :class="{ active: opt.value === modelValue }"
          role="option"
          :aria-selected="opt.value === modelValue"
          @click="pick(opt)"
        >
          <span class="u-grow u-ellipsis">{{ opt.label }}</span>
          <span v-if="opt.hint" class="g-select-hint">{{ opt.hint }}</span>
          <GIcon v-if="opt.value === modelValue" name="check" :size="13" class="g-select-check" />
        </button>
        <div v-if="!options.length" class="g-select-empty">无选项</div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.g-select { position: relative; display: inline-block; min-width: 120px; }
.g-select.disabled { opacity: .55; }

.g-select-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  background: var(--input-bg);
  border: 1px solid var(--glass-border);
  border-radius: var(--r-ctrl);
  color: var(--text);
  transition: border-color var(--dur-1) ease, box-shadow var(--dur-1) ease;
}
.s-md .g-select-trigger { height: 32px; padding: 0 10px 0 12px; font-size: 12.5px; }
.s-sm .g-select-trigger { height: 26px; padding: 0 8px 0 10px; font-size: 11.5px; border-radius: var(--r-sm); }
.g-select-trigger:hover:not(:disabled) { border-color: var(--glass-border-strong); }
.open .g-select-trigger, .g-select-trigger:focus-visible {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.g-select-label.placeholder { color: var(--text-3); }
.g-select-arrow {
  flex: none;
  color: var(--text-3);
  transition: transform var(--dur-2) var(--ease-out);
}
.open .g-select-arrow { transform: rotate(180deg); }

.g-select-pop {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  z-index: var(--z-modal);
  max-height: 260px;
  overflow-y: auto;
  padding: 4px;
  border-radius: var(--r-ctrl);
  background:
    linear-gradient(180deg, var(--glass-hi) 0%, transparent 80px),
    var(--glass-bg-2);
  backdrop-filter: blur(24px) saturate(1.6);
  -webkit-backdrop-filter: blur(24px) saturate(1.6);
  border: 1px solid var(--glass-border-strong);
  box-shadow: var(--shadow-pop);
}
.g-select-opt {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border-radius: var(--r-sm);
  font-size: 12px;
  color: var(--text);
  text-align: left;
  transition: background var(--dur-1) ease;
}
.g-select-opt:hover { background: var(--neutral-soft); }
.g-select-opt.active { color: var(--accent-text); background: var(--accent-softer); }
.g-select-check { flex: none; }
.g-select-hint { flex: none; font-size: 11px; color: var(--text-3); }
.g-select-empty { padding: 12px; text-align: center; color: var(--text-3); font-size: 12px; }
</style>
