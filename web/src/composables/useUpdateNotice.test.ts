/* useUpdateNotice 单元测试：更新公告 / 下载状态机 / 通知去重 / 初始化对账。

   模块为单例（模块级 refs），测试通过 vi.resetModules() + 动态 import 获取
   全新实例；@/api/client 与 useToast 全部 mock。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Settings, SettingsResp, UpdateDownloadStatus, UpdateInfo } from '@/api/client'

const { toastMock } = vi.hoisted(() => ({
  toastMock: { info: vi.fn(), error: vi.fn(), success: vi.fn() },
}))

vi.mock('@/composables/useToast', () => ({ useToast: () => toastMock }))

vi.mock('@/api/client', () => ({
  checkUpdate: vi.fn(),
  getUpdateStatus: vi.fn(),
  getSettings: vi.fn(),
  getUpdateNoticePending: vi.fn(),
  startUpdateDownload: vi.fn(),
  pauseUpdateDownload: vi.fn(),
  resumeUpdateDownload: vi.fn(),
  cancelUpdateDownload: vi.fn(),
  applyUpdate: vi.fn(),
}))

function makeInfo(over: Partial<UpdateInfo> = {}): UpdateInfo {
  return {
    ok: true, disabled: false, current_version: '0.13.3', version_type: 'stable',
    latest_version: '0.14.0', update_available: true, can_auto_update: true,
    release_url: 'https://github.com/x/y', published_at: '', body: '更新说明',
    checked_at: 0, error: '', asset_name: 'a.zip', asset_url: 'u', asset_size: 1,
    ...over,
  }
}

function makeStatus(over: Partial<UpdateDownloadStatus> = {}): UpdateDownloadStatus {
  return { state: 'idle', received: 0, total: 0, error: '', version: '', path: '', ...over }
}

/** checkUpdate 的完整响应包装（{ ok, update }） */
function infoRes(info: UpdateInfo): { ok: true; update: UpdateInfo } {
  return { ok: true, update: info }
}

/** getSettings 的完整响应包装（SettingsResp） */
function settingsRes(settings: Settings): SettingsResp {
  return { ok: true, settings, public_url: '', mcp_url: '' }
}

function makeSettings(over: Partial<Settings> = {}): Settings {
  return {
    mode: 'local', domain: '', host: '0.0.0.0', port: 8000, auth_token: '',
    autostart: false, start_to_tray: false, close_to_tray: false, minimize_to_tray: false,
    theme_mode: 'system', mcp_auto_start: false, mcp_transport: 'sse', mcp_host: '0.0.0.0',
    mcp_port: 8001, mcp_token: '', key_strategy: 'round-robin', rate_limit_rpm: 90,
    rate_limit_max_wait: 1.0, usage_cache_ttl: 60, endpoint_rpm: {}, cache_ttls: {},
    anomaly_thresholds: {}, log_retention_days: 90, notify_webhook: '', notify_tray: true,
    notify_interval_minutes: 5, mcp_default_parameters: {}, mcp_human_id: '',
    mcp_project_id: '', proxy_auto_start: false, proxy_host: '0.0.0.0', proxy_port: 8002,
    proxy_token: '', auto_backup_enabled: false, auto_backup_interval_days: 1,
    auto_backup_keep: 7, update_check_enabled: true, update_repo: 'zylyes/tavily-key',
    update_check_interval_hours: 24, update_check_interval_unit: 'hour',
    ...over,
  }
}

beforeEach(async () => {
  vi.resetModules()   // useUpdateNotice 单例状态重置
  toastMock.info.mockClear()
  toastMock.error.mockClear()
  toastMock.success.mockClear()
  // vi.mock 工厂的 mock 实例跨测试共享（resetModules 不重置），需手动清空
  const api = await import('@/api/client')
  vi.mocked(api.checkUpdate).mockClear()
  vi.mocked(api.getUpdateStatus).mockClear()
  vi.mocked(api.getSettings).mockClear()
  vi.mocked(api.getUpdateNoticePending).mockClear()
  vi.mocked(api.startUpdateDownload).mockClear()
  vi.mocked(api.pauseUpdateDownload).mockClear()
  vi.mocked(api.resumeUpdateDownload).mockClear()
  vi.mocked(api.cancelUpdateDownload).mockClear()
  vi.mocked(api.applyUpdate).mockClear()
})

afterEach(() => {
  vi.useRealTimers()
})

/** 动态加载最新模块实例 + client mock（同一次 resetModules 后的同一缓存）。 */
async function load() {
  const api = await import('@/api/client')
  const mod = await import('@/composables/useUpdateNotice')
  return { api: vi.mocked(api), mod }
}

describe('纯工具', () => {
  it('fmtBytes 边界', async () => {
    const { mod } = await load()
    expect(mod.fmtBytes(0)).toBe('0 B')
    expect(mod.fmtBytes(1023)).toBe('1023 B')
    expect(mod.fmtBytes(1024)).toBe('1.0 KB')
    expect(mod.fmtBytes(1536)).toBe('1.5 KB')
    expect(mod.fmtBytes(1024 * 1024)).toBe('1.0 MB')
    expect(mod.fmtBytes(3 * 1024 * 1024 * 1024)).toBe('3.0 GB')
  })

  it('updatePct：总字节未知返回 -1，否则百分比', async () => {
    const { mod } = await load()
    expect(mod.updatePct()).toBe(-1)
    mod.updateDl.value = makeStatus({ total: 0 })
    expect(mod.updatePct()).toBe(-1)
    mod.updateDl.value = makeStatus({ received: 50, total: 200 })
    expect(mod.updatePct()).toBe(25)
    mod.updateDl.value = makeStatus({ received: 300, total: 200 })
    expect(mod.updatePct()).toBe(100)  // 封顶 100
  })

  it('versionType：后端字段优先，缺失回退本地推断', async () => {
    const { mod } = await load()
    mod.update.value = makeInfo({ version_type: 'beta' })
    expect(mod.versionType.value).toBe('beta')
    expect(mod.versionTypeLabel.value).toBe('Beta 版')
    mod.update.value = makeInfo({ version_type: 'stable' })
    expect(mod.versionType.value).toBe('stable')
    expect(mod.versionTypeLabel.value).toBe('正式版')
    // 回退：无 version_type 字段 → 按版本字符串含 - 判定
    mod.update.value = { ...makeInfo(), version_type: undefined as never }
    expect(mod.versionType.value).toBe('stable')
    mod.update.value = { ...makeInfo({ current_version: '0.14.0-beta.2' }), version_type: undefined as never }
    expect(mod.versionType.value).toBe('beta')
  })
})

describe('doCheckUpdate（手动检查）', () => {
  it('禁用 → info 提示，不弹公告', async () => {
    const { api, mod } = await load()
    api.checkUpdate.mockResolvedValue(infoRes(makeInfo({ disabled: true, ok: false })))
    await mod.doCheckUpdate()
    expect(toastMock.info).toHaveBeenCalled()
    expect(mod.noticeOpen.value).toBe(false)
  })

  it('失败 → error 提示', async () => {
    const { api, mod } = await load()
    api.checkUpdate.mockResolvedValue(infoRes(makeInfo({ ok: false, error: 'net down' })))
    await mod.doCheckUpdate(true)
    expect(toastMock.error).toHaveBeenCalled()
    expect(mod.noticeOpen.value).toBe(false)
  })

  it('发现新版本 → 弹公告 + 关闭 mini + 登记去重版本', async () => {
    const { api, mod } = await load()
    api.checkUpdate.mockResolvedValue(infoRes(makeInfo()))
    mod.miniOpen.value = true
    await mod.doCheckUpdate()
    expect(toastMock.success).toHaveBeenCalled()
    expect(mod.noticeOpen.value).toBe(true)
    expect(mod.miniOpen.value).toBe(false)
  })

  it('已是最新 → success 提示', async () => {
    const { api, mod } = await load()
    api.checkUpdate.mockResolvedValue(infoRes(makeInfo({ update_available: false })))
    await mod.doCheckUpdate()
    expect(toastMock.success).toHaveBeenCalledWith('已是最新版本 0.13.3')
    expect(mod.noticeOpen.value).toBe(false)
  })
})

describe('startDownload + 下载状态机', () => {
  it('非 idle 时拒绝并提示', async () => {
    const { api, mod } = await load()
    mod.updateFlow.value = 'downloading'
    await mod.startDownload()
    expect(api.startUpdateDownload).not.toHaveBeenCalled()
    expect(toastMock.info).toHaveBeenCalled()
  })

  it('成功 → downloading + 开轮询；完成 → ready + toast', async () => {
    vi.useFakeTimers()
    const { api, mod } = await load()
    api.startUpdateDownload.mockResolvedValue({ ok: true })
    api.getUpdateStatus
      .mockResolvedValueOnce({ ok: true, status: makeStatus({ state: 'downloading', received: 10, total: 100 }) })
      .mockResolvedValueOnce({ ok: true, status: makeStatus({ state: 'done', received: 100, total: 100, version: '0.14.0' }) })
    await mod.startDownload()
    expect(mod.updateFlow.value).toBe('downloading')
    expect(mod.noticeOpen.value).toBe(true)
    expect(mod.miniOpen.value).toBe(false)
    await vi.advanceTimersByTimeAsync(800)   // 第 1 次轮询：downloading
    expect(mod.updateFlow.value).toBe('downloading')
    await vi.advanceTimersByTimeAsync(800)   // 第 2 次轮询：done
    expect(mod.updateFlow.value).toBe('ready')
    expect(toastMock.success).toHaveBeenCalled()
  })

  it('失败 → error + 停轮询 + toast', async () => {
    vi.useFakeTimers()
    const { api, mod } = await load()
    api.startUpdateDownload.mockResolvedValue({ ok: true })
    api.getUpdateStatus
      .mockResolvedValueOnce({ ok: true, status: makeStatus({ state: 'downloading' }) })
      .mockResolvedValueOnce({ ok: true, status: makeStatus({ state: 'error', error: 'boom' }) })
    await mod.startDownload()
    await vi.advanceTimersByTimeAsync(800)   // 第 1 次轮询：downloading
    await vi.advanceTimersByTimeAsync(800)   // 第 2 次轮询：error
    expect(mod.updateFlow.value).toBe('error')
    expect(toastMock.error).toHaveBeenCalledWith('下载失败：boom')
    // 已停轮询：再 advance 不再调用
    api.getUpdateStatus.mockClear()
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.getUpdateStatus).not.toHaveBeenCalled()
  })

  it('取消 → idle', async () => {
    vi.useFakeTimers()
    const { api, mod } = await load()
    api.startUpdateDownload.mockResolvedValue({ ok: true })
    api.getUpdateStatus
      .mockResolvedValueOnce({ ok: true, status: makeStatus({ state: 'downloading' }) })
      .mockResolvedValueOnce({ ok: true, status: makeStatus({ state: 'cancelled' }) })
    await mod.startDownload()
    await vi.advanceTimersByTimeAsync(800)   // 第 1 次轮询：downloading
    await vi.advanceTimersByTimeAsync(800)   // 第 2 次轮询：cancelled
    expect(mod.updateFlow.value).toBe('idle')
    expect(mod.updateDl.value).toBeNull()
  })

  it('开始下载失败 → 回 idle + error toast', async () => {
    const { api, mod } = await load()
    api.startUpdateDownload.mockResolvedValue({ ok: false, error: '仅打包版' })
    await mod.startDownload()
    expect(mod.updateFlow.value).toBe('idle')
    expect(toastMock.error).toHaveBeenCalled()
  })
})

describe('togglePause / cancelDownload / doApplyUpdate', () => {
  it('togglePause：paused 时调 resume，否则调 pause', async () => {
    const { api, mod } = await load()
    mod.updateDl.value = makeStatus({ state: 'paused' })
    api.resumeUpdateDownload.mockResolvedValue({ ok: true })
    await mod.togglePause()
    expect(api.resumeUpdateDownload).toHaveBeenCalled()
    mod.updateDl.value = makeStatus({ state: 'downloading' })
    api.pauseUpdateDownload.mockResolvedValue({ ok: true })
    await mod.togglePause()
    expect(api.pauseUpdateDownload).toHaveBeenCalled()
  })

  it('cancelDownload 成功 → idle', async () => {
    const { api, mod } = await load()
    api.cancelUpdateDownload.mockResolvedValue({ ok: true })
    mod.updateFlow.value = 'downloading'
    await mod.cancelDownload()
    expect(mod.updateFlow.value).toBe('idle')
    expect(toastMock.info).toHaveBeenCalled()
  })

  it('doApplyUpdate：非 ready 不调用；失败回 ready', async () => {
    const { api, mod } = await load()
    await mod.doApplyUpdate()   // idle
    expect(api.applyUpdate).not.toHaveBeenCalled()
    mod.updateFlow.value = 'ready'
    api.applyUpdate.mockResolvedValue({ ok: false, error: '更新包未就绪' })
    await mod.doApplyUpdate()
    expect(mod.updateFlow.value).toBe('ready')
    expect(toastMock.error).toHaveBeenCalled()
  })

  it('doApplyUpdate 成功 → applying', async () => {
    const { api, mod } = await load()
    mod.updateFlow.value = 'ready'
    api.applyUpdate.mockResolvedValue({ ok: true })
    await mod.doApplyUpdate()
    expect(mod.updateFlow.value).toBe('applying')
    expect(toastMock.info).toHaveBeenCalled()
  })
})

describe('initUpdateNotice（启动对账）', () => {
  it('后端 downloading → 恢复下载态并续轮询', async () => {
    vi.useFakeTimers()
    const { api, mod } = await load()
    api.getUpdateStatus.mockResolvedValue({ ok: true, status: makeStatus({ state: 'downloading', received: 30, total: 100 }) })
    api.getSettings.mockResolvedValue(settingsRes(makeSettings({ update_check_enabled: false, update_check_interval_hours: 24 })))
    api.getUpdateNoticePending.mockResolvedValue({ ok: true, version: '' })
    await mod.initUpdateNotice()
    expect(mod.updateFlow.value).toBe('downloading')
    expect(mod.updateDl.value?.received).toBe(30)
    // 续轮询：覆盖为 done 后推进两个周期触发回调
    api.getUpdateStatus.mockResolvedValue({ ok: true, status: makeStatus({ state: 'done', received: 100, total: 100, version: '0.14.0' }) })
    await vi.advanceTimersByTimeAsync(800)
    await vi.advanceTimersByTimeAsync(800)
    expect(mod.updateFlow.value).toBe('ready')
  })

  it('后端 done → ready（刷新页面恢复完成态）', async () => {
    const { api, mod } = await load()
    api.getUpdateStatus.mockResolvedValue({ ok: true, status: makeStatus({ state: 'done', version: '0.14.0' }) })
    api.getSettings.mockResolvedValue(settingsRes(makeSettings({ update_check_enabled: false, update_check_interval_hours: 24 })))
    await mod.initUpdateNotice()
    expect(mod.updateFlow.value).toBe('ready')
  })

  it('后端 error → error', async () => {
    const { api, mod } = await load()
    api.getUpdateStatus.mockResolvedValue({ ok: true, status: makeStatus({ state: 'error', error: 'boom' }) })
    api.getSettings.mockResolvedValue(settingsRes(makeSettings({ update_check_enabled: false, update_check_interval_hours: 24 })))
    await mod.initUpdateNotice()
    expect(mod.updateFlow.value).toBe('error')
  })

  it('自动检查关闭时不发 silentCheck', async () => {
    const { api, mod } = await load()
    api.getUpdateStatus.mockResolvedValue({ ok: true, status: makeStatus() })
    api.getSettings.mockResolvedValue(settingsRes(makeSettings({ update_check_enabled: false, update_check_interval_hours: 24 })))
    await mod.initUpdateNotice()
    expect(api.checkUpdate).not.toHaveBeenCalled()
  })

  it('自动检查开启 + 有更新 → 弹 mini（右下角通知）', async () => {
    const { api, mod } = await load()
    api.getUpdateStatus.mockResolvedValue({ ok: true, status: makeStatus() })
    api.getSettings.mockResolvedValue(settingsRes(makeSettings({ update_check_enabled: true, update_check_interval_hours: 24 })))
    api.checkUpdate.mockResolvedValue(infoRes(makeInfo()))
    await mod.initUpdateNotice()
    expect(mod.miniOpen.value).toBe(true)
    expect(mod.noticeOpen.value).toBe(false)
  })

  it('手动检查已登记的版本，自动检查不再弹 mini（去重）', async () => {
    const { api, mod } = await load()
    api.checkUpdate.mockResolvedValue(infoRes(makeInfo()))
    await mod.doCheckUpdate()   // 登记 promptedVersions
    expect(mod.noticeOpen.value).toBe(true)
    mod.noticeOpen.value = false
    mod.miniOpen.value = false
    // initUpdateNotice 的 silentCheck 命中同版本 → 不再弹 mini
    api.getUpdateStatus.mockResolvedValue({ ok: true, status: makeStatus() })
    api.getSettings.mockResolvedValue(settingsRes(makeSettings({ update_check_enabled: true, update_check_interval_hours: 24 })))
    api.checkUpdate.mockResolvedValue(infoRes(makeInfo()))
    await mod.initUpdateNotice()
    expect(mod.miniOpen.value).toBe(false)
  })

  it('closeMini 关闭过的版本不再自动弹', async () => {
    vi.useFakeTimers()
    const { api, mod } = await load()
    // 先触发 mini（autoCheck 路径）
    api.getUpdateStatus.mockResolvedValue({ ok: true, status: makeStatus() })
    api.getSettings.mockResolvedValue(settingsRes(makeSettings({ update_check_enabled: true })))
    api.getUpdateNoticePending.mockResolvedValue({ ok: true, version: '' })
    api.checkUpdate.mockResolvedValue(infoRes(makeInfo()))
    await mod.initUpdateNotice()
    expect(mod.miniOpen.value).toBe(true)
    // 用户关闭 → 登记 dismissed
    mod.closeMini()
    expect(mod.miniOpen.value).toBe(false)
    // 24h 后 autoCheck 再次 silentCheck → dismissed 版本不再弹
    await vi.advanceTimersByTimeAsync(24 * 3600 * 1000)
    expect(mod.miniOpen.value).toBe(false)
  })
})

describe('refreshAutoCheckSettings（设置保存后即时生效）', () => {
  it('interval 非法 clamp 后按小时调度（1h 后触发 silentCheck）', async () => {
    vi.useFakeTimers()
    const { api, mod } = await load()
    api.getSettings.mockResolvedValue(settingsRes(makeSettings({ update_check_enabled: true, update_check_interval_hours: 0 })))
    await mod.refreshAutoCheckSettings()
    // interval 0 → clamp 到 24h（非 0 即触发）
    api.checkUpdate.mockResolvedValue(infoRes(makeInfo({ update_available: false })))
    await vi.advanceTimersByTimeAsync(24 * 3600 * 1000)
    expect(api.checkUpdate).toHaveBeenCalled()
  })

  it('关闭自动检查后不设定时器', async () => {
    vi.useFakeTimers()
    const { api, mod } = await load()
    api.getSettings.mockResolvedValue(settingsRes(makeSettings({ update_check_enabled: false, update_check_interval_hours: 24 })))
    await mod.refreshAutoCheckSettings()
    api.checkUpdate.mockClear()
    await vi.advanceTimersByTimeAsync(25 * 3600 * 1000)
    expect(api.checkUpdate).not.toHaveBeenCalled()
  })
})
