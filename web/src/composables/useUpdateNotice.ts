/* useUpdateNotice —— 更新公告 / 下载 / 通知的全局共享状态（App.vue 与 SettingsView 共用单例）。

   交互规则：
   - 主窗口打开时自动检查到新版本 → 右下角悬浮通知（miniOpen），不弹系统通知；
     点通知主体恢复公告弹窗，头部 × 彻底关闭（该版本本次会话不再自动弹）。
   - 主窗口打开时手动点「检查更新」发现新版本 → 直接弹出公告弹窗。
   - 主窗口未打开时自动 / 托盘手动检查 → 后端发系统通知（托盘气泡），点击后打开
     主窗口并标记 /api/update/notice-pending；前端轮询到后弹出公告弹窗。
*/
import { computed, ref } from 'vue'
import {
  applyUpdate,
  cancelUpdateDownload,
  checkUpdate,
  getUpdateNoticePending,
  getUpdateStatus,
  pauseUpdateDownload,
  resumeUpdateDownload,
  startUpdateDownload,
  type UpdateDownloadStatus,
  type UpdateInfo,
} from '@/api/client'
import { useToast } from '@/composables/useToast'

const toast = useToast()

// ── 共享状态（模块级单例）──────────────────────────────────
export const update = ref<UpdateInfo | null>(null)
export const updateBusy = ref(false)
/** 更新公告弹窗（新版本 release notes） */
export const noticeOpen = ref(false)
/** 更新公告最小化后的右下角悬浮通知 */
export const miniOpen = ref(false)
export const updateFlow = ref<'idle' | 'downloading' | 'ready' | 'applying' | 'error'>('idle')
export const updateDl = ref<UpdateDownloadStatus | null>(null)

let updatePollTimer: number | undefined
/** 已自动提示过右下角通知的版本（会话级去重） */
const promptedVersions = new Set<string>()
/** 用户通过 × 关闭过的版本（该版本本次会话不再自动弹通知） */
const dismissedVersions = new Set<string>()
let started = false

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** 版本类型（beta/正式版）：后端 version_type 优先，回退本地判断 */
export const versionType = computed<'stable' | 'beta'>(() => {
  const vt = update.value?.version_type
  if (vt === 'beta' || vt === 'stable') return vt
  const v = update.value?.current_version ?? ''
  return /[-+](beta|alpha|rc|pre|dev)/i.test(v) || v.includes('-') ? 'beta' : 'stable'
})
export const versionTypeLabel = computed(() => (versionType.value === 'beta' ? 'Beta 版' : '正式版'))

export function fmtBytes(n: number): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

/** 下载进度百分比（总字节未知时返回 -1） */
export function updatePct(): number {
  const s = updateDl.value
  if (!s || !s.total) return -1
  return Math.min(100, Math.round((s.received / s.total) * 100))
}

/** 打开 GitHub 链接：桌面版经 pywebview 桥接用系统浏览器打开，浏览器模式 window.open */
export function openRelease(url: string): void {
  if (!url) return
  if (window.pywebview?.api?.open_external) {
    window.pywebview.api.open_external(url)
  } else {
    window.open(url, '_blank', 'noopener')
  }
}

/** 手动「检查更新」（设置页按钮）：发现新版本 → 直接弹出公告弹窗 */
export async function doCheckUpdate(force = false): Promise<void> {
  if (updateBusy.value) return
  updateBusy.value = true
  try {
    const r = await checkUpdate(force)
    update.value = r.update
    if (r.update.disabled) {
      toast.info('更新检查已禁用（GitHub 仓库未配置）')
    } else if (!r.update.ok) {
      toast.error(`更新检查失败：${r.update.error || '网络错误'}`)
    } else if (r.update.update_available) {
      toast.success(`发现新版本 ${r.update.latest_version}（当前 ${r.update.current_version}）`)
      // 手动检查（主窗口打开）：直接展示公告弹窗
      miniOpen.value = false
      noticeOpen.value = true
    } else {
      toast.success(`已是最新版本 ${r.update.current_version}`)
    }
  } catch (e) {
    toast.error(`更新检查失败：${errMsg(e)}`)
  } finally {
    updateBusy.value = false
  }
}

/** 静默检查（App.vue 启动/轮询）：发现新版本 → 右下角通知，不弹系统通知 */
async function silentCheck(): Promise<void> {
  try {
    const r = await checkUpdate(false)
    update.value = r.update
    maybePromptMini(r.update)
  } catch { /* 静默：检查失败不打扰 */ }
}

/** 自动轮询检查：发现新版本 → 右下角通知（按版本去重 + 已关闭版本不再弹） */
async function autoCheck(): Promise<void> {
  await silentCheck()
}

function maybePromptMini(info: UpdateInfo | null): void {
  if (!info || !info.update_available) return
  const v = info.latest_version || ''
  if (!v || promptedVersions.has(v) || dismissedVersions.has(v)) return
  promptedVersions.add(v)
  miniOpen.value = true
}

// ── 公告弹窗 / 右下角通知开关 ───────────────────────────────
export function openNotice(): void {
  noticeOpen.value = true
}

/** 最小化公告弹窗为右下角悬浮通知 */
export function minimizeNotice(): void {
  noticeOpen.value = false
  miniOpen.value = true
}

/** 点击通知主体：恢复公告弹窗 */
export function restoreNotice(): void {
  miniOpen.value = false
  noticeOpen.value = true
}

/** 关闭右下角通知（该版本本次会话不再自动弹；下载若进行中不受影响） */
export function closeMini(): void {
  miniOpen.value = false
  const v = update.value?.latest_version || ''
  if (v) dismissedVersions.add(v)
}

// ── 自动更新下载流程（进度显示在公告弹窗/右下角通知内）──────
export async function startDownload(): Promise<void> {
  if (updateFlow.value !== 'idle') return
  updateFlow.value = 'downloading'
  updateDl.value = null
  try {
    const r = await startUpdateDownload()
    if (!r.ok) {
      toast.error(`开始下载失败：${r.error || '未知错误'}`)
      updateFlow.value = 'idle'
      return
    }
    noticeOpen.value = true
    miniOpen.value = false
    pollUpdateStatus()
  } catch (e) {
    toast.error(`开始下载失败：${errMsg(e)}`)
    updateFlow.value = 'idle'
  }
}

function pollUpdateStatus(): void {
  window.clearInterval(updatePollTimer)
  updatePollTimer = window.setInterval(async () => {
    try {
      const r = await getUpdateStatus()
      updateDl.value = r.status
      const st = r.status.state
      if (st === 'done') {
        window.clearInterval(updatePollTimer)
        updateFlow.value = 'ready'
        toast.success(`新版 ${r.status.version} 已下载完成，可重启应用`)
      } else if (st === 'error') {
        window.clearInterval(updatePollTimer)
        updateFlow.value = 'error'
      } else if (st === 'cancelled' || st === 'idle') {
        // 已取消：恢复初始状态
        window.clearInterval(updatePollTimer)
        updateFlow.value = 'idle'
        updateDl.value = null
      }
      // starting / downloading / paused 继续轮询
    } catch {
      /* 轮询失败忽略，等待下一次 */
    }
  }, 800)
}

/** 暂停 / 继续下载 */
export async function togglePause(): Promise<void> {
  const paused = updateDl.value?.state === 'paused'
  try {
    const r = paused ? await resumeUpdateDownload() : await pauseUpdateDownload()
    if (!r.ok) {
      toast.error(`${paused ? '继续' : '暂停'}失败：${r.error || '未知错误'}`)
    }
  } catch (e) {
    toast.error(`${paused ? '继续' : '暂停'}失败：${errMsg(e)}`)
  }
}

/** 取消下载并清理 */
export async function cancelDownload(): Promise<void> {
  try {
    const r = await cancelUpdateDownload()
    if (!r.ok) {
      toast.error(`取消失败：${r.error || '未知错误'}`)
      return
    }
    window.clearInterval(updatePollTimer)
    updateFlow.value = 'idle'
    updateDl.value = null
    toast.info('已取消下载')
  } catch (e) {
    toast.error(`取消失败：${errMsg(e)}`)
  }
}

/** 下载失败后重试 */
export async function retryDownload(): Promise<void> {
  updateFlow.value = 'idle'
  updateDl.value = null
  await startDownload()
}

/** 应用更新并重启应用（调用后当前进程将被结束） */
export async function doApplyUpdate(): Promise<void> {
  if (updateFlow.value !== 'ready') return
  updateFlow.value = 'applying'
  try {
    const r = await applyUpdate()
    if (!r.ok) {
      toast.error(`应用更新失败：${r.error || '未知错误'}`)
      updateFlow.value = 'ready'
      return
    }
    toast.info('正在重启应用，请稍候…', 6000)
    // 进程即将被结束，页面将断开
  } catch (e) {
    toast.error(`应用更新失败：${errMsg(e)}`)
    updateFlow.value = 'ready'
  }
}

// ── 全局初始化（App.vue onMounted 调用一次）────────────────
/** 启动全局轮询：立即静默检查 + 轮询系统通知标记 + 定期自动检查。 */
export function initUpdateNotice(): void {
  if (started) return
  started = true
  silentCheck()
  // 系统通知（托盘气泡）点击后打开主窗口：轮询待展示公告标记
  window.setInterval(pollNoticePending, 10 * 1000)
  // 主窗口打开时的「自动检查」：发现新版本弹右下角通知
  window.setInterval(autoCheck, 10 * 60 * 1000)
}

/** 轮询系统通知点击标记：非空 → 弹出公告弹窗 */
async function pollNoticePending(): Promise<void> {
  try {
    const r = await getUpdateNoticePending()
    if (!r.version) return
    // 从系统通知打开：确保 update 数据新鲜后展示公告弹窗
    try {
      const cr = await checkUpdate(false)
      update.value = cr.update
    } catch { /* 忽略：用已有数据 */ }
    miniOpen.value = false
    noticeOpen.value = true
  } catch { /* 静默 */ }
}
