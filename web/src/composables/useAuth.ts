import { reactive, readonly } from 'vue'
import {
  getAuthToken,
  notifyAuthResolved,
  onUnauthorized,
  probeToken,
  setAuthToken,
} from '@/api/client'

/* ═══════════════════════════════════════════════════════════════
   useAuth —— 访问令牌状态、登录/登出、401 事件接线
   模块级单例。App.vue 挂载时调用 initAuth() 一次；
   LoginModal 用 login() 提交；视图无需关心鉴权（client 自动重试）。
   ═══════════════════════════════════════════════════════════════ */

interface AuthState {
  /** 当前是否要求输入令牌（有请求被 401 挂起） */
  required: boolean
  /** 登录失败提示（LoginModal 展示） */
  error: string
  /** login() 验证中 */
  busy: boolean
}

const state = reactive<AuthState>({
  required: false,
  error: '',
  busy: false,
})

let initialized = false

/** App.vue onMounted 调用一次：把 401 事件接到登录模态。 */
export function initAuth(): void {
  if (initialized) return
  initialized = true
  onUnauthorized(() => {
    state.error = ''
    state.required = true
  })
}

export function useAuth() {
  /**
   * 提交令牌：先探测后端是否接受，成功才持久化并放行挂起请求。
   * 返回是否成功；失败时 state.error 有提示。
   */
  async function login(candidate: string): Promise<boolean> {
    const token = candidate.trim()
    if (!token || state.busy) return false
    state.busy = true
    state.error = ''
    try {
      const ok = await probeToken(token)
      if (!ok) {
        state.error = '令牌无效或无法连接服务，请重试'
        return false
      }
      setAuthToken(token)
      state.required = false
      notifyAuthResolved()
      return true
    } finally {
      state.busy = false
    }
  }

  /** 清除已存令牌（设置页「修改令牌」等场景；清除后下个 401 会再次弹窗） */
  function logout(): void {
    setAuthToken('')
  }

  return {
    state: readonly(state),
    token: getAuthToken,   // 函数引用：每次调用读最新值
    login,
    logout,
  }
}
