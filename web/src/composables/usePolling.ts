import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

/* ═══════════════════════════════════════════════════════════════
   usePolling —— 轮询 composable：视图挂载自动开始、卸载自动停止
   ═══════════════════════════════════════════════════════════════ */

export interface PollingOptions {
  /** 轮询间隔 ms（默认 5000） */
  interval?: number
  /** 挂载后是否立即执行一次（默认 true） */
  immediate?: boolean
}

export interface RefreshOptions {
  /** 手动刷新：跳过 inFlight 防重入（在途请求完成后仍会补跑一次） */
  force?: boolean
  /** loading/refreshing 状态最短保持毫秒数，给刷新按钮可感知的反馈 */
  minBusyMs?: number
}

export interface PollingResult<T> {
  /** 最近一次成功响应（未成功过为 null） */
  data: Ref<T | null>
  /** 首次加载中（data 还没有值） */
  loading: Ref<boolean>
  /** 后续刷新中（已有数据，静默刷新） */
  refreshing: Ref<boolean>
  /** 最近一次错误（成功时清空） */
  error: Ref<Error | null>
  /** 手动触发一次刷新（按钮点击等，可传 force / minBusyMs） */
  refresh: (opts?: RefreshOptions) => Promise<void>
  start: () => void
  stop: () => void
}

export function usePolling<T>(fn: () => Promise<T>, options: PollingOptions = {}): PollingResult<T> {
  const { interval = 5000, immediate = true } = options

  const data = ref<T | null>(null) as Ref<T | null>
  const loading = ref(false)
  const refreshing = ref(false)
  const error = ref<Error | null>(null)

  let timer: ReturnType<typeof setInterval> | null = null
  let running = false
  let inFlight = false
  let pendingManual = false

  async function refresh(opts: RefreshOptions = {}): Promise<void> {
    const { force = false, minBusyMs = 0 } = opts
    if (inFlight) {
      // 上一轮未结束：自动轮询静默跳过防堆积；手动刷新（force）排队等补跑
      if (!force) return
      pendingManual = true
      return
    }
    inFlight = true
    if (data.value === null) loading.value = true
    else refreshing.value = true
    const started = performance.now()
    try {
      data.value = await fn()
      error.value = null
    } catch (e) {
      error.value = e instanceof Error ? e : new Error(String(e))
    } finally {
      // busy 状态最短保持 minBusyMs，让刷新按钮有可感知的反馈
      const remain = minBusyMs - (performance.now() - started)
      if (remain > 0) await new Promise((r) => setTimeout(r, remain))
      inFlight = false
      loading.value = false
      refreshing.value = false
      if (pendingManual) {
        pendingManual = false
        void refresh({ force: true, minBusyMs })
      }
    }
  }

  function start(): void {
    if (running) return
    running = true
    void refresh()
    timer = setInterval(() => void refresh(), interval)
  }

  function stop(): void {
    running = false
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  onMounted(() => {
    if (immediate) start()
  })
  onBeforeUnmount(stop)

  return { data, loading, refreshing, error, refresh, start, stop }
}
