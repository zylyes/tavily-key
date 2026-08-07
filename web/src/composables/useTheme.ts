import { computed, ref } from 'vue'
import { getSettings, saveSettings } from '@/api/client'

/* ═══════════════════════════════════════════════════════════════
   useTheme —— 主题模式读写 / 解析 / 系统联动
   协议与 index.html 内联引导脚本一致（localStorage.tavilyTheme，
   <html data-theme / data-theme-mode>），模块级单例。
   主题模式同时持久化到后端 config.json（theme_mode），供 Python 端
   决定 WebView 启动开屏背景色。
   ═══════════════════════════════════════════════════════════════ */

export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

const THEME_KEY = 'tavilyTheme'
const VALID: ThemeMode[] = ['system', 'light', 'dark']

function readMode(): ThemeMode {
  try {
    const m = localStorage.getItem(THEME_KEY) as ThemeMode | null
    if (m && VALID.includes(m)) return m
  } catch { /* 忽略 */ }
  return 'system'
}

function systemTheme(): ResolvedTheme {
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

function applyToDom(mode: ThemeMode): void {
  const resolved = mode === 'system' ? systemTheme() : mode
  const root = document.documentElement
  root.setAttribute('data-theme', resolved)
  root.setAttribute('data-theme-mode', mode)
}

// 模块级单例状态（所有组件共享）
const mode = ref<ThemeMode>(readMode())

/** 当前模式（system/light/dark，原始存储值） */
export function useTheme() {
  /** 解析后的实际主题（light/dark） */
  const resolved = computed<ResolvedTheme>(() =>
    mode.value === 'system' ? systemTheme() : mode.value)

  function setMode(m: ThemeMode): void {
    if (!VALID.includes(m)) return
    mode.value = m
    try {
      localStorage.setItem(THEME_KEY, m)
    } catch { /* 忽略 */ }
    applyToDom(m)
    // 同步到后端（config.json）：Python 端启动开屏背景色据此调整
    saveSettings({ theme_mode: m }).catch(() => { /* 忽略 */ })
  }

  return { mode, resolved, setMode }
}

// 首次启动：若后端已记录主题模式而本地无值（如曾在浏览器里设置过、或
// WebView 持久化修复前设置的），取后端值初始化，保证开屏背景与页面一致。
getSettings()
  .then((r) => {
    const m = r.settings.theme_mode
    if (!VALID.includes(m)) return
    try {
      if (!localStorage.getItem(THEME_KEY)) {
        mode.value = m
        localStorage.setItem(THEME_KEY, m)
        applyToDom(m)
      }
    } catch { /* 忽略 */ }
  })
  .catch(() => { /* 忽略 */ })

// 系统主题变化联动（mode=system 时）。index.html 引导脚本也监听了一次
// （覆盖 app 加载前的窗口期），这里接管运行期并同步 ref 状态。
try {
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
    if (mode.value === 'system') applyToDom('system')
  })
} catch { /* 忽略 */ }

// 多窗口/多标签同步
try {
  window.addEventListener('storage', (e) => {
    if (e.key === THEME_KEY) {
      mode.value = readMode()
      applyToDom(mode.value)
    }
  })
} catch { /* 忽略 */ }
