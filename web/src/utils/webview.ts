import { ref } from 'vue'

/* ═══════════════════════════════════════════════════════════════
   webview.ts —— pywebview 套壳集成（逐字协议，参考旧前端
   docs/archive/dashboard-legacy.html 1162–1202 行）
   - window.pywebview.api: minimize / toggle_maximize / close /
     resize(dir) / start_drag
   - 桥接脚本在页面脚本之后才注入（派发 pywebviewready 事件），
     必须等事件后再接线
   - 套壳内给 document.body 加 webview 类（缩放热区仅此时显示）
   ═══════════════════════════════════════════════════════════════ */

/** 当前是否在 pywebview 套壳内（响应式，App 壳据此显示窗口控制） */
export const isWebview = ref(false)

export function inWebview(): boolean {
  return typeof window !== 'undefined' && !!(window.pywebview && window.pywebview.api)
}

/**
 * 套壳环境接线：App.vue onMounted 调用一次。
 * 加 body.webview 类；AppHeader / ResizeHandles 内部自行判断 isWebview。
 */
export function setupWebviewBridge(): void {
  const connect = () => {
    if (!inWebview()) return
    isWebview.value = true
    document.body.classList.add('webview')
  }
  if (inWebview()) connect()
  else window.addEventListener('pywebviewready', connect, { once: true })
}

/** 浏览器模式下的兜底提示文案（与旧版一致） */
export const WEBVIEW_ONLY_MSG = '窗口控制（最小化/最大化/关闭/缩放）仅在桌面版可用。请运行 Tavily.exe 打开桌面窗口。'

export function winMinimize(): void {
  window.pywebview?.api.minimize()
}
export function winToggleMaximize(): void {
  window.pywebview?.api.toggle_maximize()
}
export function winClose(): void {
  window.pywebview?.api.close()
}
/** dir ∈ top/bottom/left/right/top-left/top-right/bottom-left/bottom-right */
export function winResize(dir: string): void {
  window.pywebview?.api.resize(dir)
}
export function winStartDrag(): void {
  window.pywebview?.api.start_drag()
}

/**
 * 桌面版「另存为」备份：系统保存对话框（默认目录 = 程序根目录），
 * 后端直接写入所选位置。无桥接时返回 undefined（调用方自行降级）。
 */
export function saveBackupAs(filename?: string): Promise<SaveBackupAsResult> | undefined {
  return window.pywebview?.api?.save_backup_as?.(filename)
}
