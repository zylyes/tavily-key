<script setup lang="ts">
import GIcon from './GIcon.vue'

/* GInput —— 输入框（v-model）
   用法：
     <GInput v-model="kw" placeholder="搜索…" clearable @enter="run" />
     <GInput v-model="token" type="password" mono /> */
withDefaults(defineProps<{
  modelValue: string | number
  type?: string
  placeholder?: string
  disabled?: boolean
  /** 等宽字体（密钥/令牌/地址） */
  mono?: boolean
  clearable?: boolean
  size?: 'sm' | 'md'
  /** 左侧图标名（见 @/icons） */
  icon?: string
  id?: string
  name?: string
  autocomplete?: string
}>(), {
  type: 'text',
  size: 'md',
})

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'enter'): void
  (e: 'clear'): void
  (e: 'change'): void
}>()

function onInput(ev: Event): void {
  emit('update:modelValue', (ev.target as HTMLInputElement).value)
}
function onKeydown(ev: Event): void {
  const ke = ev as KeyboardEvent
  if (ke.key === 'Enter') emit('enter')
}
/** 值提交（失焦 / 回车）时触发，供 @change 自动保存 */
function onChange(): void {
  emit('change')
}
function clear(): void {
  emit('update:modelValue', '')
  emit('clear')
}
</script>

<template>
  <div class="g-input" :class="[`s-${size}`, { mono, disabled, 'has-icon': !!icon }]">
    <GIcon v-if="icon" :name="icon" :size="14" class="g-input-icon" />
    <input
      :id="id"
      :name="name"
      :type="type"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      :autocomplete="autocomplete"
      @input="onInput"
      @keydown="onKeydown"
      @change="onChange"
    />
    <button
      v-if="clearable && modelValue !== '' && !disabled"
      type="button"
      class="g-input-clear"
      aria-label="清空"
      tabindex="-1"
      @click="clear"
    >
      <GIcon name="x" :size="12" />
    </button>
  </div>
</template>

<style scoped>
.g-input {
  position: relative;
  display: inline-flex;
  align-items: center;
  width: 100%;
  background: var(--input-bg);
  border: 1px solid var(--glass-border);
  border-radius: var(--r-ctrl);
  transition: border-color var(--dur-1) ease, box-shadow var(--dur-1) ease, background var(--dur-1) ease;
}
.g-input:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.g-input.disabled { opacity: .55; }

.g-input input {
  width: 100%;
  background: none;
  border: none;
  outline: none;
  color: var(--text);
}
.g-input input::placeholder { color: var(--text-3); }

.s-md { height: 32px; }
.s-md input { padding: 0 12px; font-size: 12.5px; }
.s-sm { height: 26px; border-radius: var(--r-sm); }
.s-sm input { padding: 0 10px; font-size: 11.5px; }

.mono input { font-family: var(--font-mono); font-size: 12px; letter-spacing: .02em; }

.g-input-icon {
  flex: none;
  margin-left: 10px;
  color: var(--text-3);
}
.has-icon input { padding-left: 6px !important; }

.g-input-clear {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-right: 8px;
  border-radius: 50%;
  color: var(--text-3);
  background: var(--neutral-soft);
  transition: color var(--dur-1) ease, background var(--dur-1) ease;
}
.g-input-clear:hover { color: var(--text); background: var(--glass-border-strong); }
</style>
