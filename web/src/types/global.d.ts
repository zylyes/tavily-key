/// <reference types="vite/client" />

/* pywebview 桥接（js_api 注入的全局对象与事件） */

/** 桌面版「另存为」备份结果（后端 _WindowApi.save_backup_as） */
interface SaveBackupAsResult {
  ok: boolean
  path?: string       // 成功：备份实际写入路径
  cancelled?: boolean // 用户取消对话框
  error?: string
}

interface PyWebviewApi {
  minimize(): void
  toggle_maximize(): void
  close(): void
  resize(direction: string): void
  start_drag(): void
  save_backup_as(filename?: string): Promise<SaveBackupAsResult>
  /** 用系统默认浏览器打开外部链接（后端 _WindowApi.open_external） */
  open_external(url: string): Promise<{ ok: boolean; error?: string }>
}

/* File System Access API（Chrome/Edge 支持，TS DOM lib 未内置）——
   浏览器模式备份「另存为」使用 */
interface SaveFilePickerOptions {
  suggestedName?: string
  types?: Array<{ description?: string; accept: Record<string, string[]> }>
}
interface SaveFileHandle {
  createWritable(): Promise<SaveFileWritable>
}
interface SaveFileWritable {
  write(data: Blob | BufferSource | string): Promise<void>
  close(): Promise<void>
}

interface Window {
  pywebview?: { api: PyWebviewApi }
  showSaveFilePicker?: (options?: SaveFilePickerOptions) => Promise<SaveFileHandle>
}

interface WindowEventMap {
  pywebviewready: Event
}
