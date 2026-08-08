/* ═══════════════════════════════════════════════════════════════
   API 客户端 —— app/dashboard.py 全部 /api/* 端点的类型化封装
   ─────────────────────────────────────────────────────────────
   契约来源：app/dashboard.py + app/key_pool.py + app/settings.py
   （API 契约不可改；后端字段变动时同步此文件，并在结果中报告）

   鉴权协议（与旧版一致）：
   - token 存 localStorage.tavilyAuthToken
   - 每个请求自动带 X-Auth-Token 头
   - 收到 401：挂起当前请求 → 触发全局登录事件 → 登录成功后自动重试
   ═══════════════════════════════════════════════════════════════ */

// ── 鉴权状态与 401 事件总线 ──────────────────────────────────
const AUTH_KEY = 'tavilyAuthToken'

export function getAuthToken(): string {
  try {
    return localStorage.getItem(AUTH_KEY) || ''
  } catch {
    return ''
  }
}

export function setAuthToken(token: string): void {
  try {
    if (token) localStorage.setItem(AUTH_KEY, token)
    else localStorage.removeItem(AUTH_KEY)
  } catch { /* 隐私模式等场景忽略 */ }
}

type UnauthorizedHandler = () => void
const unauthorizedHandlers = new Set<UnauthorizedHandler>()
const authWaiters: Array<() => void> = []
let authPromptActive = false

/** 注册 401 监听（App 层用来弹出登录模态）。返回取消注册函数。 */
export function onUnauthorized(cb: UnauthorizedHandler): () => void {
  unauthorizedHandlers.add(cb)
  return () => unauthorizedHandlers.delete(cb)
}

/** 登录成功后由 useAuth 调用：放行所有因 401 挂起的请求。 */
export function notifyAuthResolved(): void {
  authPromptActive = false
  const waiters = authWaiters.splice(0)
  for (const resolve of waiters) resolve()
}

function waitForAuth(): Promise<void> {
  return new Promise((resolve) => {
    authWaiters.push(resolve)
    if (!authPromptActive) {
      authPromptActive = true
      for (const cb of unauthorizedHandlers) cb()
    }
  })
}

/** 用候选 token 探测后端是否接受（登录模态提交时调用，不影响挂起队列）。 */
export async function probeToken(candidate: string): Promise<boolean> {
  try {
    const resp = await fetch('/api/settings', {
      headers: candidate ? { 'X-Auth-Token': candidate } : {},
    })
    return resp.status !== 401
  } catch {
    return false
  }
}

// ── 错误类型 ────────────────────────────────────────────────
export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** 从非 2xx 响应提取 error/detail（JSON 或纯文本），与旧版 respErrorText 对齐。 */
async function respErrorText(r: Response, fallback: string): Promise<string> {
  try {
    const raw = await r.text()
    try {
      const obj = JSON.parse(raw)
      if (obj && typeof obj === 'object' && (obj.error || obj.detail)) {
        return String(obj.error || obj.detail)
      }
    } catch { /* 非 JSON */ }
    if (raw) return raw
  } catch { /* 忽略 */ }
  return fallback
}

// ── fetch 包装 ──────────────────────────────────────────────
async function rawFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = getAuthToken()
  if (token) headers.set('X-Auth-Token', token)
  return fetch(path, { ...init, headers })
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}

/** JSON 请求：401 时挂起等待登录，登录成功后自动重试（对调用方透明）。 */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let resp = await rawFetch(path, init)
  while (resp.status === 401) {
    await waitForAuth()
    resp = await rawFetch(path, init)
  }
  if (!resp.ok) throw new ApiError(resp.status, await respErrorText(resp, `HTTP ${resp.status}`))
  return (await resp.json()) as T
}

export interface BlobResult {
  blob: Blob
  filename: string
}

/** Blob 请求（CSV 导出 / 备份下载）：同样的 401 挂起-重试语义。 */
async function requestBlob(path: string, init: RequestInit = {}): Promise<BlobResult> {
  let resp = await rawFetch(path, init)
  while (resp.status === 401) {
    await waitForAuth()
    resp = await rawFetch(path, init)
  }
  if (!resp.ok) throw new ApiError(resp.status, await respErrorText(resp, `HTTP ${resp.status}`))
  const dispo = resp.headers.get('Content-Disposition') || ''
  const m = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(dispo)
  const filename = m ? decodeURIComponent(m[1].replace(/"$/, '')) : 'download'
  return { blob: await resp.blob(), filename }
}

/** 触发浏览器下载（views 直接调用）。 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 4000)
}

// ═══════════════════════════════════════════════════════════
// 类型定义（与后端 JSON 一一对应）
// ═══════════════════════════════════════════════════════════

/** key_pool._apikey_to_dict */
export interface ApiKeyInfo {
  masked: string
  is_active: boolean
  is_exhausted: boolean
  request_count: number
  error_count: number
  credits_used: number
  credits_limit: number
  usage_pct: number
  last_used_at: number        // unix 秒，0 = 从未使用
  added_at: number            // unix 秒
  last_error: string
  plan: string
  plan_usage: number
  plan_limit: number
  research_usage: number
  usage_synced_at: number     // unix 秒，0 = 未同步
}

/** request_log 行（SQLite 返回 success 为 0/1） */
export interface RequestLog {
  id: number
  key_masked: string
  endpoint: string
  credits_consumed: number
  success: number
  error_msg: string
  latency_ms: number
  request_id: string
  usage_source: string        // response | unknown | none
  source: string              // mcp | proxy | cli | ''
  is_client_error?: number
  project_id?: string         // MCP 请求的 mcp_project_id 归属（代理请求为空）
  created_at: number          // unix 秒
}

/** key_pool.get_aggregate */
export interface Aggregate {
  total_keys: number
  active_keys: number
  exhausted_count: number
  total_limit: number
  total_used: number
  remaining: number
  usage_pct: number
}

/** key_pool.detect_anomalies 元素 */
export interface Anomaly {
  masked: string
  is_active: boolean
  is_exhausted: boolean
  flags: string[]             // exhausted/near_exhausted/suspected_leak/high_error_rate/stale/slow
  reasons: string[]
  usage_pct: number
  credits_used: number
  credits_limit: number
}

/** key_pool.get_usage_trend */
export interface TrendPoint {
  date: string                // YYYY-MM-DD（本地时区）
  requests: number
  success: number
  failed: number
  credits: number
  endpoints: Record<string, number>
}
export interface UsageTrend {
  days: number
  points: TrendPoint[]
}

/** GET /api/stats */
export interface Stats {
  keys: ApiKeyInfo[]
  total_keys: number
  active_keys: number
  total_requests: number
  total_errors: number
  total_credits: number
  recent_24h: Record<string, { success: number; failed: number }>
  logs: RequestLog[]
  aggregate: Aggregate
  anomalies: Anomaly[]
}

/** key_pool.check_health 元素 */
export interface HealthResult {
  masked?: string
  alive?: boolean
  latency_ms?: number
  error?: string
  error_category?: string     // ok/auth/quota/rate/bad_request/empty/other/fatal
  skipped?: boolean
}

/** key_pool.sync_usage 元素 */
export interface UsageSyncResult {
  masked: string
  ok: boolean
  usage?: number
  limit?: number | null
  search_usage?: number
  extract_usage?: number
  crawl_usage?: number
  map_usage?: number
  research_usage?: number
  plan?: string
  plan_usage?: number
  plan_limit?: number
  recovered?: boolean
  skipped?: boolean
  error?: string
}

/** mcp_server.list_research_tasks 元素 */
export interface ResearchTask {
  request_id: string
  masked: string
  status: string              // submitted/pending/running/completed/failed/error/cancelled/unknown
  content?: string | null     // 内容摘要（≤200 字符）
  error?: string
  key_used?: string
  cached?: boolean
}

/** GET /api/logs 查询参数 */
export interface LogsQuery {
  endpoint?: string
  key?: string                // key_masked
  status?: '' | 'success' | 'failed'
  days?: number               // 0 = 全部
  source?: string             // mcp | proxy | cli | ''
  project?: string            // request_log.project_id（MCP 项目归属）
  limit?: number              // 1-1000，默认 200
  offset?: number             // ≥0，默认 0
}
export interface LogsPage {
  ok: true
  logs: RequestLog[]
  total: number
  limit: number
  offset: number
}

/** settings.DEFAULTS（POST /api/settings 只接受这些键，见 validate_patch） */
export interface Settings {
  mode: 'server' | 'local'
  domain: string
  host: string
  port: number
  auth_token: string
  autostart: boolean
  start_to_tray: boolean
  close_to_tray: boolean
  minimize_to_tray: boolean
  mcp_auto_start: boolean
  mcp_transport: 'stdio' | 'sse' | 'streamable-http'
  mcp_host: string
  mcp_port: number
  mcp_token: string
  key_strategy: 'round-robin' | 'least-used'
  rate_limit_rpm: number
  rate_limit_max_wait: number
  usage_cache_ttl: number
  endpoint_rpm: Record<string, number>
  cache_ttls: Record<string, number>
  anomaly_thresholds: Record<string, number>
  log_retention_days: number
  notify_webhook: string
  notify_tray: boolean
  notify_interval_minutes: number
  mcp_default_parameters: Record<string, unknown>
  mcp_human_id: string
  mcp_project_id: string
  proxy_auto_start: boolean
  proxy_host: string
  proxy_port: number
  proxy_token: string
  auto_backup_enabled: boolean
  auto_backup_interval_days: number
  auto_backup_keep: number
  update_check_enabled: boolean   // 是否自动检查更新
  update_check_interval_hours: number  // 自动检查间隔（小时，0=关闭）
  update_check_interval_unit: 'hour' | 'day' | 'week' | 'month'  // 面板展示单位
  theme_mode: 'system' | 'light' | 'dark'   // 界面颜色模式（WebView 开屏背景据此调整）
  [key: string]: unknown      // 后端新增字段时的逃生门
}
export type SettingsPatch = Partial<Settings>

export interface SettingsResp {
  ok: true
  settings: Settings
  public_url: string
  mcp_url: string
}

/** GET /api/mcp/status（mcp_manager.status + 脱敏 token） */
export interface McpStatus {
  ok: true
  running: boolean
  pid: number | null
  transport: string
  host: string
  port: number
  url: string
  urls: Record<string, string>   // local/ip/hostname/hostname_local（stdio 时为空）
  network: boolean               // 非 stdio 即 true
  auto_start: boolean
  auto_restarts: number          // 会话内看门狗自动重启次数
  token: string                  // 脱敏值（abcd****wxyz），完整值只在 /api/settings
  token_set: boolean
}

/** GET /api/proxy/status（proxy_manager.status + 脱敏 token） */
export interface ProxyStatus {
  ok: true
  running: boolean
  pid: number | null
  host: string
  port: number
  url: string
  urls: Record<string, string>
  auto_start: boolean
  auto_restarts: number          // 会话内看门狗自动重启次数
  token: string                  // 脱敏值（abcd****wxyz），完整值只在 /api/settings
  token_set: boolean
}

/** POST /api/mcp|proxy/start|stop 返回 */
export interface ServiceActionResult {
  ok: boolean
  error?: string
  status?: McpStatus & ProxyStatus  // 后端按服务返回对应结构
}

// ═══════════════════════════════════════════════════════════
// 端点函数
// ═══════════════════════════════════════════════════════════

// ── 总览 ──────────────────────────────────────────────────
/** GET /api/stats —— 全量统计（keys+聚合+近50条日志+异常） */
export const getStats = () => request<Stats>('/api/stats')

// ── Key 管理（POST 均为 JSON body） ────────────────────────
/** POST /api/keys/add，body {keys: string[]}，返回实际新增数量 */
export const addKeys = (keys: string[]) =>
  request<{ ok: true; added: number }>('/api/keys/add', jsonInit('POST', { keys }))

/** POST /api/keys/remove，body {masked} */
export const removeKey = (masked: string) =>
  request<{ ok: true }>('/api/keys/remove', jsonInit('POST', { masked }))

/** POST /api/keys/activate，body {masked} */
export const activateKey = (masked: string) =>
  request<{ ok: true }>('/api/keys/activate', jsonInit('POST', { masked }))

/** POST /api/keys/deactivate，body {masked, reason?} */
export const deactivateKey = (masked: string, reason = 'manual') =>
  request<{ ok: true }>('/api/keys/deactivate', jsonInit('POST', { masked, reason }))

// ── 健康检查 ───────────────────────────────────────────────
/** POST /api/health —— 全量并发探测（较慢，逐个进度请用 healthCheckOne） */
export const healthCheckAll = () =>
  request<{ results: HealthResult[] }>('/api/health', jsonInit('POST'))

/** POST /api/health/one，body {masked} */
export const healthCheckOne = (masked: string) =>
  request<{ ok: true; result: HealthResult }>('/api/health/one', jsonInit('POST', { masked }))

/** GET /api/keys/anomalies */
export const getAnomalies = () =>
  request<{ ok: true; anomalies: Anomaly[] }>('/api/keys/anomalies')

// ── 用量 ───────────────────────────────────────────────────
/** GET /api/usage/aggregate */
export const getUsageAggregate = () =>
  request<{ ok: true; aggregate: Aggregate }>('/api/usage/aggregate')

/** GET /api/usage/trend?days=&source=&project=（days 1-90） */
export const getUsageTrend = (days = 7, source = '', project = '') =>
  request<{ ok: true; trend: UsageTrend }>(
    `/api/usage/trend?days=${encodeURIComponent(days)}&source=${encodeURIComponent(source)}`
      + `&project=${encodeURIComponent(project)}`)

/** GET /api/projects —— 请求日志中出现过的项目 ID（面板筛选下拉） */
export const getProjects = () =>
  request<{ ok: true; projects: string[] }>('/api/projects')

/** POST /api/keys/usage-sync —— 同步所有 active key 官方用量（较慢） */
export const syncUsageAll = () =>
  request<{ ok: true; synced: number; failed: number; results: UsageSyncResult[] }>(
    '/api/keys/usage-sync', jsonInit('POST'))

/** POST /api/keys/usage-sync/one，body {masked} */
export const syncUsageOne = (masked: string) =>
  request<{ ok: true; result: UsageSyncResult }>('/api/keys/usage-sync/one', jsonInit('POST', { masked }))

// ── Research 任务 ──────────────────────────────────────────
/** GET /api/research/tasks?limit=（1-200，默认 50） */
export const getResearchTasks = (limit = 50) =>
  request<{ ok: true; tasks: ResearchTask[] }>(`/api/research/tasks?limit=${encodeURIComponent(limit)}`)

/** POST /api/research/retry —— 用原任务参数重试失败任务，返回新提交的 request_id */
export const retryResearchTask = (requestId: string) =>
  request<{ ok: true; request_id: string; task: unknown }>(
    '/api/research/retry', jsonInit('POST', { request_id: requestId }))

// ── 日志 ───────────────────────────────────────────────────
function logsQueryString(params: LogsQuery): string {
  const p = new URLSearchParams()
  if (params.endpoint) p.set('endpoint', params.endpoint)
  if (params.key) p.set('key', params.key)
  if (params.status) p.set('status', params.status)
  if (params.days) p.set('days', String(params.days))
  if (params.source) p.set('source', params.source)
  if (params.project) p.set('project', params.project)
  if (params.limit !== undefined) p.set('limit', String(params.limit))
  if (params.offset !== undefined) p.set('offset', String(params.offset))
  const s = p.toString()
  return s ? `?${s}` : ''
}

/** GET /api/logs —— 筛选 + 分页 */
export const getLogs = (params: LogsQuery = {}) =>
  request<LogsPage>(`/api/logs${logsQueryString(params)}`)

/** GET /api/logs/export.csv —— 按筛选导出 CSV（BlobResult，调用方 saveBlob） */
export const exportLogsCsv = (params: LogsQuery = {}) =>
  requestBlob(`/api/logs/export.csv${logsQueryString(params)}`)

/** POST /api/logs/clear —— 按筛选清理日志（空条件=清空全部），返回删除条数 */
export const clearLogs = (params: LogsQuery = {}) =>
  request<{ ok: true; deleted: number }>('/api/logs/clear', jsonInit('POST', {
    endpoint: params.endpoint ?? '',
    key: params.key ?? '',
    status: params.status ?? '',
    source: params.source ?? '',
    project: params.project ?? '',
    days: params.days ?? 0,
  }))

/** GET /api/audit/export.zip —— 全量请求审计包（zip：请求日志 + 池状态 + 汇总） */
export const exportAuditZip = () =>
  requestBlob('/api/audit/export.zip')

/** GET /api/docs/tree —— wiki 文档目录树（分类 → 文档列表） */
export interface DocItem {
  name: string
  path: string
  title: string
}
export interface DocCategory {
  category: string
  docs: DocItem[]
}
export const getDocsTree = () =>
  request<{ ok: true; tree: DocCategory[] }>('/api/docs/tree')

/** GET /api/docs?path= —— 读取 wiki 文档 markdown 原文 + 标题 */
export interface WikiDoc {
  path: string
  title: string
  content: string
}
export const getDoc = (path: string) =>
  request<{ ok: true; doc: WikiDoc }>(`/api/docs?path=${encodeURIComponent(path)}`)

// ── 设置 ───────────────────────────────────────────────────
/** GET /api/settings */
export const getSettings = () => request<SettingsResp>('/api/settings')

/** POST /api/settings —— 白名单字段补丁（validate_patch 校验），400 时抛 ApiError */
export const saveSettings = (patch: SettingsPatch) =>
  request<SettingsResp>('/api/settings', jsonInit('POST', patch))

// ── 开机自启 ───────────────────────────────────────────────
/** GET /api/autostart */
export const getAutostart = () =>
  request<{ ok: true; enabled: boolean; command: string }>('/api/autostart')

/** POST /api/autostart，body {enabled} */
export const setAutostart = (enabled: boolean) =>
  request<{ ok: true; enabled: boolean }>('/api/autostart', jsonInit('POST', { enabled }))

// ── MCP 服务 ───────────────────────────────────────────────
/** GET /api/mcp/status */
export const getMcpStatus = () => request<McpStatus>('/api/mcp/status')
/** POST /api/mcp/start（stdio 模式会返回 ok:false + error 说明） */
export const startMcp = () => request<ServiceActionResult>('/api/mcp/start', jsonInit('POST'))
/** POST /api/mcp/stop */
export const stopMcp = () => request<ServiceActionResult>('/api/mcp/stop', jsonInit('POST'))
/** POST /api/mcp/token/generate —— 生成随机 MCP 访问令牌（返回完整明文，仅此一次） */
export const generateMcpToken = () =>
  request<{ ok: true; token: string }>('/api/mcp/token/generate', jsonInit('POST'))

// ── 搜索代理 ───────────────────────────────────────────────
/** GET /api/proxy/status */
export const getProxyStatus = () => request<ProxyStatus>('/api/proxy/status')
/** POST /api/proxy/start */
export const startProxy = () => request<ServiceActionResult>('/api/proxy/start', jsonInit('POST'))
/** POST /api/proxy/stop */
export const stopProxy = () => request<ServiceActionResult>('/api/proxy/stop', jsonInit('POST'))
/** POST /api/proxy/token/generate —— 生成随机代理密钥（返回完整明文，仅此一次） */
export const generateProxyToken = () =>
  request<{ ok: true; token: string }>('/api/proxy/token/generate', jsonInit('POST'))

// ── 备份 / 恢复 ────────────────────────────────────────────
/** POST /api/backup —— 下载 data/ 备份 zip（BlobResult，调用方 saveBlob） */
export const backupData = () => requestBlob('/api/backup', { method: 'POST' })

/** POST /api/restore —— 上传备份 zip 恢复（原始字节 body，非 multipart） */
export const restoreData = (file: File | Blob) =>
  request<{ ok: true; restored: number }>('/api/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  })

// ── GitHub 更新检查 ──────────────────────────────────────────
/** GET /api/update/check —— updater.check_update() 结果 */
export interface UpdateInfo {
  ok: boolean                  // 请求成功（False = 网络失败或已禁用）
  disabled: boolean            // update_repo 未配置 = 关闭更新检查
  current_version: string
  version_type: 'stable' | 'beta'   // 当前版本类型（pre-release 后缀 → beta）
  latest_version: string
  update_available: boolean
  can_auto_update: boolean     // 打包版（sys.frozen）支持下载安装
  release_url: string
  published_at: string         // ISO8601，可能为空
  body: string                 // release notes（可能为空）
  checked_at: number           // unix 秒
  error: string
  asset_name: string           // 打包产物文件名（Tavily-*-win64.zip），空=无产物
  asset_url: string
  asset_size: number           // 字节
}

/** GET /api/update/check?force=1（force 传 true 强制刷新网络，跳过缓存） */
export const checkUpdate = (force = false) =>
  request<{ ok: true; update: UpdateInfo }>(
    `/api/update/check${force ? '?force=1' : ''}`)

// ── 自动更新（下载 / 状态 / 暂停 / 取消 / 应用，仅打包版）──────
/** GET /api/update/status —— updater.get_download_status() */
export interface UpdateDownloadStatus {
  state: 'idle' | 'starting' | 'downloading' | 'paused' | 'done' | 'error' | 'cancelled'
  received: number             // 已下载字节
  total: number                // 总字节（未知为 0）
  error: string
  version: string              // 正在下载/已就绪的版本
  path: string                 // 解压后新版目录（done 时）
}

/** POST /api/update/download —— 后台下载最新打包 zip */
export const startUpdateDownload = () =>
  request<{ ok: boolean; error?: string }>('/api/update/download', jsonInit('POST'))

/** GET /api/update/status —— 轮询下载进度 */
export const getUpdateStatus = () =>
  request<{ ok: true; status: UpdateDownloadStatus }>('/api/update/status')

/** POST /api/update/pause —— 暂停下载（保持连接，可继续） */
export const pauseUpdateDownload = () =>
  request<{ ok: boolean; error?: string }>('/api/update/pause', jsonInit('POST'))

/** POST /api/update/resume —— 继续已暂停的下载 */
export const resumeUpdateDownload = () =>
  request<{ ok: boolean; error?: string }>('/api/update/resume', jsonInit('POST'))

/** POST /api/update/cancel —— 取消下载并清理临时文件 */
export const cancelUpdateDownload = () =>
  request<{ ok: boolean; error?: string }>('/api/update/cancel', jsonInit('POST'))

/** POST /api/update/apply —— 应用更新并重启（调用后当前进程将结束） */
export const applyUpdate = () =>
  request<{ ok: boolean; error?: string }>('/api/update/apply', jsonInit('POST'))

/** GET /api/update/announcement —— 本次更新公告（一次性读取，读取后后端清除） */
export interface UpdateAnnouncement {
  version: string
  body: string
  applied_at: number
}
export const getUpdateAnnouncement = () =>
  request<{ ok: true; announcement: UpdateAnnouncement | null }>('/api/update/announcement')

/** GET /api/update/notice-pending —— 系统通知点击后待展示公告的版本（一次性读取清除） */
export const getUpdateNoticePending = () =>
  request<{ ok: true; version: string }>('/api/update/notice-pending')
