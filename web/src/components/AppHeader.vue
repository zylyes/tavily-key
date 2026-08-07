<script setup lang="ts">
import { useTheme, type ThemeMode } from '@/composables/useTheme'
import { useToast } from '@/composables/useToast'
import GIcon from './GIcon.vue'
import {
  isWebview, WEBVIEW_ONLY_MSG, winClose, winMinimize, winStartDrag, winToggleMaximize,
} from '@/utils/webview'

/* AppHeader —— 顶部玻璃栏：品牌 + 主题切换 + 窗口控制
   空白处拖动窗口（350ms 双击防抖）、双击切换最大化（仅套壳）。 */

const { mode, setMode } = useTheme()
const toast = useToast()

const THEME_OPTIONS: Array<{ value: ThemeMode; icon: string; label: string }> = [
  { value: 'system', icon: 'monitor', label: '跟随系统' },
  { value: 'light', icon: 'sun', label: '浅色' },
  { value: 'dark', icon: 'moon', label: '深色' },
]

function needWebview(): boolean {
  if (isWebview.value) return false
  toast.info(WEBVIEW_ONLY_MSG)
  return true
}

function onMin(): void { if (!needWebview()) winMinimize() }
function onMax(): void { if (!needWebview()) winToggleMaximize() }
function onClose(): void { if (!needWebview()) winClose() }

// ── 拖动 / 双击最大化（对齐旧版 dashboard.html 1186–1196 行） ──
let lastDown = 0
function onMousedown(e: MouseEvent): void {
  if (!isWebview.value) return
  if ((e.target as HTMLElement).closest('button,input,textarea,select,a,[data-no-drag]')) return
  const now = Date.now()
  if (now - lastDown < 350) return   // 双击，交给 dblclick
  lastDown = now
  winStartDrag()
}
function onDblclick(e: MouseEvent): void {
  if (!isWebview.value) return
  if ((e.target as HTMLElement).closest('button,input,textarea,select,a,[data-no-drag]')) return
  winToggleMaximize()
}
</script>

<template>
  <header class="app-header" @mousedown="onMousedown" @dblclick="onDblclick">
    <div class="brand u-flex u-gap-3" data-no-drag>
      <img class="brand-logo" :src="'/logo.png'" alt="Tavily" draggable="false" />
      <div class="brand-titles">
        <span class="brand-title">Tavily Key Pool</span>
        <span class="brand-sub">Tavily API 密钥池管理面板</span>
      </div>
    </div>

    <div class="header-right u-flex u-gap-3" data-no-drag>
      <!-- 主题三段切换 -->
      <div class="theme-seg" role="radiogroup" aria-label="主题">
        <button
          v-for="opt in THEME_OPTIONS"
          :key="opt.value"
          type="button"
          class="theme-seg-btn"
          :class="{ active: mode === opt.value }"
          role="radio"
          :aria-checked="mode === opt.value"
          :title="opt.label"
          @click="setMode(opt.value)"
        >
          <GIcon :name="opt.icon" :size="13" />
          <span class="theme-seg-label">{{ opt.label }}</span>
        </button>
      </div>

      <!-- 窗口控制（套壳内可用；浏览器模式点击给提示） -->
      <div class="win-controls" :class="{ disabled: !isWebview }">
        <button type="button" class="win-btn" aria-label="最小化" title="最小化" @click="onMin">
          <GIcon name="minus" :size="14" />
        </button>
        <button type="button" class="win-btn" aria-label="最大化/还原" title="最大化/还原" @click="onMax">
          <GIcon name="maximize" :size="13" />
        </button>
        <button type="button" class="win-btn win-close" aria-label="关闭" title="关闭" @click="onClose">
          <GIcon name="close" :size="14" />
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: var(--z-header);
  height: var(--header-h);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 14px 0 16px;
  background:
    linear-gradient(180deg, var(--glass-hi) 0%, transparent 100%),
    var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border-bottom: 1px solid var(--glass-border);
}

.brand { min-width: 0; }
.brand-logo {
  width: 28px;
  height: 28px;
  filter: drop-shadow(0 4px 12px rgba(109, 124, 255, .4));
}
.brand-titles {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
  min-width: 0;
}
.brand-title { font-size: 13px; font-weight: 650; letter-spacing: .02em; white-space: nowrap; }
.brand-sub { font-size: 10.5px; color: var(--text-3); white-space: nowrap; }

.header-right { flex: none; }

/* ── 主题三段控件 ── */
.theme-seg {
  display: flex;
  padding: 2px;
  gap: 2px;
  border-radius: var(--r-ctrl);
  background: var(--neutral-soft);
  border: 1px solid var(--glass-border);
}
.theme-seg-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 9px;
  border-radius: var(--r-sm);
  font-size: 11px;
  color: var(--text-3);
  transition: background var(--dur-1) ease, color var(--dur-1) ease;
}
.theme-seg-btn:hover { color: var(--text-2); }
.theme-seg-btn.active {
  background: var(--glass-bg-2);
  color: var(--text);
  box-shadow: 0 1px 4px rgba(0, 0, 0, .18), inset 0 1px 0 var(--glass-hi);
}

/* ── 窗口控制按钮 ── */
.win-controls { display: flex; gap: 2px; }
.win-controls.disabled { opacity: .5; }
.win-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 28px;
  border-radius: var(--r-sm);
  color: var(--text-2);
  transition: background var(--dur-1) ease, color var(--dur-1) ease;
}
.win-btn:hover { background: var(--neutral-soft); color: var(--text); }
.win-btn.win-close:hover { background: var(--danger-soft); color: var(--danger); }

@media (max-width: 760px) {
  .theme-seg-label { display: none; }
  .brand-sub { display: none; }
}
</style>
