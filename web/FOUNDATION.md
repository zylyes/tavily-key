# FOUNDATION.md —— 新 Dashboard 前端地基文档（视图开发代理必读）

> 本文档是 `web/` 前端的**契约文档**。后续 5 个并行代理在此基础上实现 7 个业务视图。
> **纪律**：不要修改 `package.json` / `node_modules` / 本文档列出的共享文件
> （`src/api/client.ts`、`src/components/*`、`src/composables/*`、`src/styles/*`、
> `src/App.vue`、`src/router/index.ts`、`src/icons.ts`、`src/utils/*`）。
> 如确需改动共享文件：**不要直接改**，在你的结果里报告需求，由协调方统一处理。

## 0. 工程概况

- Vite 6 + Vue 3.5（`<script setup lang="ts">`）+ TypeScript 5.8（strict）+ vue-router 4（**hash 模式**）+ echarts 5（已安装）。
- 路径别名：`@/` → `src/`（vite 与 tsconfig 均已配置）。
- 命令：`npm run dev`（5173，已代理 `/api`、`/logo.png`、`/favicon.ico` → `http://127.0.0.1:8000`）、
  `npm run build`（= `vite build && vue-tsc --noEmit`，**两者都必须过**）、`npm run typecheck`。
- `base: './'`：产物为相对路径，FastAPI 托管。**不要用 `@/assets` 静态 import 外部图片**；`/logo.png`、
  `/favicon.ico` 是后端运行时端点，模板里必须写**动态绑定** `:src="'/logo.png'"`（静态 `src="/logo.png"`
  会被 Vite 当构建期资产解析而报错）。
- 运行环境：pywebview（WebView2，无边框 1280×840，min 1024×640）+ 现代浏览器，完全离线（禁止 CDN/外部字体/图片）。
- 主题协议（与旧版一致，已实现于 `index.html` 引导脚本 + `useTheme`）：
  `localStorage.tavilyTheme ∈ {system,light,dark}`，`<html data-theme="解析值" data-theme-mode="原始值">`。

## 1. 视图开发约定

### 1.1 你要做的

每个视图代理只需**整体替换** `src/views/XxxView.vue` 一个文件（路由已配好，懒加载）：

| 路由 | 文件 | 视图 |
|---|---|---|
| `/keys`（`/` 重定向到此） | `src/views/KeysView.vue` | API Key 列表 |
| `/stats` | `src/views/StatsView.vue` | 统计 |
| `/logs` | `src/views/LogsView.vue` | 请求日志 |
| `/mcp` | `src/views/McpView.vue` | MCP 服务 |
| `/proxy` | `src/views/ProxyView.vue` | 搜索代理 |
| `/tasks` | `src/views/TasksView.vue` | Research 任务 |
| `/settings` | `src/views/SettingsView.vue` | 设置 |

视图内可以新增**视图私有**子组件/工具，放在 `src/views/parts/<视图名>/` 目录下（自建目录），
不要往 `src/components/` 加文件。

### 1.2 页面结构模板

```vue
<script setup lang="ts">
import { ref } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import GlassCard from '@/components/GlassCard.vue'
import GButton from '@/components/GButton.vue'
import Skeleton from '@/components/Skeleton.vue'
import { usePolling } from '@/composables/usePolling'
import { useToast } from '@/composables/useToast'
import { getStats } from '@/api/client'

const toast = useToast()
const { data, loading, refresh } = usePolling(getStats, { interval: 5000 })
// loading=true 仅在首次（data 尚为 null）时；后续轮询是静默的（refreshing=true）
</script>

<template>
  <div class="view">                      <!-- 根节点固定 class="view"（已有内边距/间距/居中约定） -->
    <PageHeader title="API Key 列表" desc="管理池中的 Tavily API Key">
      <template #actions>
        <GButton size="sm" @click="refresh">刷新</GButton>
      </template>
    </PageHeader>

    <GlassCard v-if="loading"><Skeleton :lines="4" /></GlassCard>
    <div v-else class="stagger">          <!-- stagger：子元素依次入场（详见 §5.3） -->
      <GlassCard title="卡片标题" desc="副标题">…</GlassCard>
      <GlassCard>…</GlassCard>
    </div>
  </div>
</template>
```

要点：
- 视图根节点用 `<div class="view">`（`.view` 已在 base.css 定义：padding 22/24/28、纵向 flex gap 16、max-width 1400 居中）。
- 首屏加载用 `<Skeleton>`；空数据用 `<EmptyState>`；错误用 `usePolling` 返回的 `error` ref 或 `toast.error()`。
- **401 不用处理**：API client 自动挂起 → 弹登录模态 → 验证后自动重试。
- 视图切换过渡（fade+slide）已在 App.vue 完成；你只管视图内的 `.stagger` 入场编排。

### 1.3 常见模式的正确写法

- **轮询**：`usePolling(fn, { interval })` 挂载自动开始、卸载自动停止、防在飞堆积；手动刷新调 `refresh()`。
  服务状态类（mcp/proxy status）建议 `interval: 5000`；趋势/日志类 10–30s。
- **写操作后刷新**：`await removeKey(m); toast.success('已删除'); await refresh()`。
- **数字**：统计卡大数字用 `<AnimatedNumber :value="n" />`（自动滚动 + tabular-nums）。
- **额度**：`<QuotaBar :pct="key.usage_pct" :used="key.credits_used" :limit="key.credits_limit" />`。
- **时间/延迟/积分**：用 `@/utils/format` 的 `fmtTs / fmtTsFull / fmtLatency / fmtNum / fmtPct / fmtCredits / fmtBytes`，
  不要各自重写格式化函数。
- **图表**：用 `useECharts`（§3.3），取色用 `chartColors()`，主题切换自动重绘。
- **下载**：`const { blob, filename } = await exportLogsCsv(params); saveBlob(blob, filename)`（`saveBlob` 从 client 导出）。
- **复制到剪贴板**：`navigator.clipboard.writeText(s)` 后 `toast.success('已复制')`（WebView2 已放行剪贴板权限）。
- **图标**：`<GIcon name="refresh" />`，可用名见 `src/icons.ts` 的 `ICONS` 键
  （key/chart/list/server/globe/beaker/settings/minus/maximize/close/sun/moon/monitor/check/copy/refresh/plus/trash/
  download/upload/search/chevronDown/chevronLeft/chevronRight/collapse/expand/play/stop/alert/info/inbox/eye/eyeOff/
  external/clock/zap/shield/x）。缺图标：在结果里报告，不要自造风格不一的图标。

## 2. API client（`src/api/client.ts`）

全部函数返回 `Promise<T>`；失败抛 `ApiError`（含 `.status`，message 已从后端 error/detail 提取）。
401 透明处理（挂起→登录→重试），**视图无需 catch 401**。

### 2.1 鉴权原语（一般只在壳层用）

```ts
getAuthToken(): string                    // localStorage.tavilyAuthToken
setAuthToken(token: string): void         // 空串 = 清除
onUnauthorized(cb: () => void): () => void // 注册 401 监听，返回取消函数
notifyAuthResolved(): void                // 登录成功后放行挂起请求（useAuth 内部已调）
probeToken(candidate: string): Promise<boolean>  // 探测 token 是否被接受
class ApiError extends Error { status: number }
saveBlob(blob: Blob, filename: string): void     // 触发浏览器下载
```

### 2.2 业务端点（签名 + 返回摘要）

| 函数 | 端点 | 返回 |
|---|---|---|
| `getStats()` | GET `/api/stats` | `Stats`：`{ keys: ApiKeyInfo[], total_keys, active_keys, total_requests, total_errors, total_credits, recent_24h: Record<endpoint,{success,failed}>, logs: RequestLog[]（近50条）, aggregate: Aggregate, anomalies: Anomaly[] }` |
| `addKeys(keys: string[])` | POST `/api/keys/add` | `{ ok, added: number }` |
| `removeKey(masked)` | POST `/api/keys/remove` | `{ ok }` |
| `activateKey(masked)` | POST `/api/keys/activate` | `{ ok }` |
| `deactivateKey(masked, reason='manual')` | POST `/api/keys/deactivate` | `{ ok }` |
| `healthCheckAll()` | POST `/api/health`（慢，全量并发探测） | `{ results: HealthResult[] }` |
| `healthCheckOne(masked)` | POST `/api/health/one` | `{ ok, result: HealthResult }` |
| `getAnomalies()` | GET `/api/keys/anomalies` | `{ ok, anomalies: Anomaly[] }` |
| `getUsageAggregate()` | GET `/api/usage/aggregate` | `{ ok, aggregate: Aggregate }` |
| `getUsageTrend(days=7, source='')` | GET `/api/usage/trend`（days 1-90） | `{ ok, trend: { days, points: TrendPoint[] } }` |
| `syncUsageAll()` | POST `/api/keys/usage-sync`（慢） | `{ ok, synced, failed, results: UsageSyncResult[] }` |
| `syncUsageOne(masked)` | POST `/api/keys/usage-sync/one` | `{ ok, result: UsageSyncResult }` |
| `getResearchTasks(limit=50)` | GET `/api/research/tasks`（1-200） | `{ ok, tasks: ResearchTask[] }` |
| `getLogs(params?: LogsQuery)` | GET `/api/logs` | `LogsPage: { ok, logs, total, limit, offset }` |
| `exportLogsCsv(params?: LogsQuery)` | GET `/api/logs/export.csv` | `BlobResult { blob, filename }`（配合 `saveBlob`） |
| `getSettings()` | GET `/api/settings` | `SettingsResp { ok, settings, public_url, mcp_url }` |
| `saveSettings(patch: SettingsPatch)` | POST `/api/settings`（白名单字段，400 抛 ApiError） | `SettingsResp` |
| `getAutostart()` | GET `/api/autostart` | `{ ok, enabled, command }` |
| `setAutostart(enabled: boolean)` | POST `/api/autostart` | `{ ok, enabled }` |
| `getMcpStatus()` | GET `/api/mcp/status` | `McpStatus` |
| `startMcp()` / `stopMcp()` | POST `/api/mcp/start|stop` | `ServiceActionResult { ok, error?, status? }`（stdio 模式 start 返回 `ok:false`+说明） |
| `getProxyStatus()` | GET `/api/proxy/status` | `ProxyStatus` |
| `startProxy()` / `stopProxy()` | POST `/api/proxy/start|stop` | `ServiceActionResult` |
| `generateProxyToken()` | POST `/api/proxy/token/generate` | `{ ok, token }`（**完整明文仅此一次**，status 接口只回脱敏值） |
| `backupData()` | POST `/api/backup` | `BlobResult`（zip，配合 `saveBlob`） |
| `restoreData(file: File\|Blob)` | POST `/api/restore`（原始字节，非 multipart） | `{ ok, restored: number }` |

### 2.3 关键类型字段（完整定义见 client.ts，字段与后端一一对应）

```ts
ApiKeyInfo   // masked, is_active, is_exhausted, request_count, error_count,
             // credits_used, credits_limit, usage_pct, last_used_at, added_at,
             // last_error, plan, plan_usage, plan_limit, research_usage, usage_synced_at
RequestLog   // id, key_masked, endpoint, credits_consumed, success(0/1!), error_msg,
             // latency_ms, request_id, usage_source(response|unknown|none), source(mcp|proxy|cli|''),
             // is_client_error?, created_at（unix 秒）
Aggregate    // total_keys, active_keys, exhausted_count, total_limit, total_used, remaining, usage_pct
Anomaly      // masked, is_active, is_exhausted, flags[], reasons[], usage_pct, credits_used, credits_limit
             // flags ∈ exhausted | near_exhausted | suspected_leak | high_error_rate | stale | slow
TrendPoint   // date('YYYY-MM-DD'), requests, success, failed, credits, endpoints: Record<string,number>
HealthResult // masked?, alive?, latency_ms?, error?, error_category?(ok|auth|quota|rate|bad_request|empty|other|fatal), skipped?
UsageSyncResult // masked, ok, usage?, limit?(null=无限额), plan?, plan_usage?, plan_limit?, recovered?, skipped?, error?
ResearchTask // request_id, masked, status(submitted|pending|running|completed|failed|error|cancelled|unknown),
             // content?(≤200字符摘要), error?, key_used?, cached?
LogsQuery    // { endpoint?, key?, status?: ''|'success'|'failed', days?, source?, limit?(≤1000), offset? }
McpStatus    // ok, running, pid, transport, host, port, url, urls: Record<string,string>, network, auto_start
ProxyStatus  // ok, running, pid, host, port, url, urls, auto_start, token(脱敏), token_set
Settings     // 见 client.ts（settings.DEFAULTS 全字段；带 [key:string]: unknown 逃生门）
```

注意：
- 时间戳均为 **unix 秒**（`fmtTs(ts)` 直接可用）。
- `RequestLog.success` 是 **0/1 数字**（SQLite），判断用 `!!log.success`。
- 积分展示语义用 `fmtCredits(log)`：失败 `-`，接口不返回 usage（Research）`—`。

## 3. Composables（`src/composables/`）

### 3.1 usePolling

```ts
const { data, loading, refreshing, error, refresh, start, stop } =
  usePolling(fn: () => Promise<T>, { interval?: number = 5000, immediate?: boolean = true })
```
- `loading`：仅首次（data 为 null）为 true；`refreshing`：后续静默刷新中为 true。
- 挂载自动开始、卸载自动停止；上一轮未结束时自动跳过，不堆积。
- 错误进入 `error` ref（不抛、不 toast），成功自动清空。

### 3.2 useTheme

```ts
const { mode, resolved, setMode } = useTheme()
// mode: Ref<'system'|'light'|'dark'>（存储值）；resolved: ComputedRef<'light'|'dark'>（实际值）
// setMode('dark')：写 localStorage + 更新 <html data-*>。Header 三段控件已接线，视图一般只读。
```

### 3.3 useECharts（`useECharts.ts`，含图表取色助手）

```ts
import { useECharts, chartColors, cssVar } from '@/composables/useECharts'

const el = ref<HTMLElement | null>(null)
const { update, resize, instance } = useECharts(el, () => {
  const c = chartColors()   // { accent, accent2, success, warn, danger, info, text, textDim, border }
  return {
    grid: { left: 40, right: 16, top: 24, bottom: 28 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: dates, axisLabel: { color: c.textDim } },
    yAxis: { type: 'value', axisLabel: { color: c.textDim }, splitLine: { lineStyle: { color: c.border } } },
    series: [{ type: 'line', data: values, lineStyle: { color: c.accent } }],
  }
})
watch(dataRef, () => update())   // 数据变化后手动 update
```
- 自动：挂载 init、容器 ResizeObserver resize、`<html data-theme>` 变化自动重 setOption（取色函数在 option 工厂内调用即可主题联动）、卸载 dispose。
- 模板：`<div ref="el" style="height: 260px"></div>`（**必须给显式高度**）。
- echarts 全量打包进共享 chunk，视图直接用即可，无需再做按需注册。

### 3.4 useAuth（壳层已接线，视图通常不用）

```ts
const { state, token, login, logout } = useAuth()
// state: { required: boolean, error: string, busy: boolean }（只读）
// login(candidate): Promise<boolean>；logout()：清除已存 token
```

### 3.5 useToast

```ts
const toast = useToast()
toast.success('已保存')            // 2.6s
toast.info('提示', 4000?)          // 默认 3.2s
toast.error('失败原因')            // 默认 4.2s
toast.dismiss(id)                  // 主动关闭
// 宿主 <GToast /> 已在 App.vue，视图直接用 composable
```

## 4. 组件（`src/components/`）

### 4.1 GlassCard —— 玻璃卡片

```ts
props: { title?: string; desc?: string; pad?: 'none'|'sm'|'md'|'lg' = 'md'; hover?: boolean = false }
slots: default（内容）、actions（头部右侧操作区）、footer
```
```vue
<GlassCard title="容量" desc="全池聚合" hover>
  <template #actions><GButton size="sm">导出</GButton></template>
  …
</GlassCard>
```

### 4.2 GButton —— 按钮

```ts
props: {
  variant?: 'primary'|'ghost'|'danger'|'warn' = 'ghost'  // primary=靛蓝紫渐变
  size?: 'sm'|'md' = 'md'                                 // sm=26px 高，md=32px
  busy?: boolean        // 加载态：显示 spinner 并屏蔽点击
  disabled?: boolean
  type?: 'button'|'submit' = 'button'
  text?: boolean        // 无边框文字按钮（表格行内操作）
}
emits: (e: 'click', ev: MouseEvent)   // busy/disabled 时不会触发
slots: default（可放 <GIcon/> + 文字）
```

### 4.3 GBadge —— 柔光胶囊

```ts
props: { type?: 'success'|'fail'|'warn'|'info'|'neutral' = 'neutral'; dot?: boolean = false }
slots: default
// <GBadge type="success" dot>运行中</GBadge>
```

### 4.4 GModal —— 模态

```ts
props: { open: boolean; title?: string; width?: string = '460px'; closable?: boolean = true }
emits: (e: 'update:open', v: boolean)、(e: 'close')
slots: default、footer（右侧按钮区）
// 已含：Teleport to body、弹簧入场、Esc/点遮罩关闭（closable=false 时禁用）、Tab 焦点圈定、打开时自动聚焦
```
```vue
<GModal v-model:open="showAdd" title="添加 Key" width="520px">
  <GInput v-model="keys" … />
  <template #footer>
    <GButton @click="showAdd = false">取消</GButton>
    <GButton variant="primary" :busy="adding" @click="submit">确定</GButton>
  </template>
</GModal>
```

### 4.5 GInput —— 输入框

```ts
props: {
  modelValue: string | number
  type?: string = 'text'        // text/password/number…
  placeholder?: string; disabled?: boolean
  mono?: boolean                // 等宽字体（密钥/令牌/地址）
  clearable?: boolean
  size?: 'sm'|'md' = 'md'
  icon?: string                 // 左侧 GIcon 名
  id?: string; name?: string; autocomplete?: string
}
emits: update:modelValue、enter、clear
```

### 4.6 GSelect —— 自定义下拉

```ts
props: {
  modelValue: string | number | null
  options: Array<{ label: string; value: string | number; hint?: string }>
  placeholder?: string = '请选择'; disabled?: boolean; size?: 'sm'|'md' = 'md'
}
emits: update:modelValue、change
// 玻璃弹出层、点外部/Esc 关闭、选中勾、hint 右侧弱提示
```

### 4.7 GSwitch —— 开关

```ts
props: { modelValue: boolean; disabled?: boolean; label?: string }
emits: update:modelValue、change
// <GSwitch v-model="autoStart" label="开机自启" @change="save" />
```

### 4.8 Skeleton —— 骨架屏

```ts
props: { lines?: number = 1; height?: string = '14px'; width?: string = '100%'; circle?: boolean }
// <Skeleton :lines="4" /> / <Skeleton height="160px" />（图表占位）
```

### 4.9 AnimatedNumber —— 数字滚动

```ts
props: {
  value: number
  duration?: number = 600       // ms，easeOutCubic
  decimals?: number = 0         // format 缺省时生效
  format?: (n: number) => string  // 优先于 decimals/prefix/suffix（可传 fmtNum）
  prefix?: string; suffix?: string
}
// <AnimatedNumber :value="agg.remaining" :format="fmtNum" suffix=" 积分" />
// prefers-reduced-motion 时直接落终值；数字自带 tabular-nums
```

### 4.10 QuotaBar —— 额度条

```ts
props: {
  pct: number                   // 0-100，自动截断；≥90 红、≥70 琥珀、否则绿
  used?: number; limit?: number // 传入后标签显示 "1,234 / 5,000（24.7%）"；limit  falsy 显示「无限额」
  height?: string = '8px'
  showLabel?: boolean = true
}
// <QuotaBar :pct="k.usage_pct" :used="k.credits_used" :limit="k.credits_limit || k.plan_limit" />
```

### 4.11 EmptyState —— 空状态

```ts
props: { title: string; desc?: string; icon?: string = 'inbox' }
slots: icon、default（操作按钮区）
```

### 4.12 PageHeader —— 页头

```ts
props: { title: string; desc?: string }
slots: actions（右侧操作区）
```

### 4.13 GIcon —— 图标

```ts
props: { name: string; size?: number|string = 16; strokeWidth?: number = 1.8 }
// 可用 name 见 src/icons.ts ICONS 键
```

## 5. 样式系统

### 5.1 CSS 变量清单（`src/styles/tokens.css`）

```
字体：  --font-sans / --font-mono
圆角：  --r-card(14) / --r-ctrl(10) / --r-sm(8) / --r-pill
动效：  --dur-1(140ms) / --dur-2(240ms) / --dur-3(400ms)
        --ease-out(cubic-bezier(.2,.8,.2,1)) / --ease-spring(cubic-bezier(.34,1.4,.4,1))
布局：  --header-h(56px) / --nav-w(208px) / --nav-w-collapsed(64px)
层级：  --z-nav 40 / --z-header 50 / --z-modal 98 / --z-login 99 / --z-toast 100 / --z-resize 9999
底色：  --bg / --bg-2 / --bg-3
文本：  --text / --text-2(次要) / --text-3(弱化)
accent：--accent / --accent-2 / --accent-text(文本用) / --accent-soft / --accent-softer
        / --on-accent / --accent-grad(渐变) / --accent-grad-soft
语义：  --success/--warn/--danger/--info + 各自 --*-soft 底色 + --neutral-soft
玻璃：  --glass-bg / --glass-bg-2(更实，弹出层用) / --glass-border / --glass-border-strong
        / --glass-hi(上缘高光) / --glass-blur(backdrop-filter 值)
其它：  --input-bg / --mask / --shadow-card / --shadow-pop / --scrollbar(-hover) / --focus-ring
```
双主题自动切换（`:root[data-theme="dark"/"light"]`），**视图只引用变量，禁止硬编码颜色**。
深浅需区分时：`[data-theme="dark"] .xxx { … }`。

### 5.2 工具类与基类（`src/styles/base.css`）

- `.glass`：玻璃面板基类（自定义面板直接套，含渐变上缘高光 + 描边 + 多层投影）。
- `.view`：视图根布局（§1.2）。
- `.u-mono`（等宽）、`.u-num`（tabular-nums）、`.u-muted`（--text-2）、`.u-dim`（--text-3）、
  `.u-ellipsis`、`.u-flex`、`.u-gap-2`、`.u-gap-3`、`.u-grow`、`.visually-hidden`。

### 5.3 动效类（`src/styles/motion.css`）

- `.stagger > *`：父容器内直接子元素依次 fade+slide 入场（前 12 个递增加延迟）——**推荐用于卡片列表/表格行**。
- `.stagger-item` + `style="--i: n"`：手动编排序号（45ms 步进）。
- `.spin`：旋转（加载图标）；`.pulse-dot`：呼吸点（运行中指示）。
- `<Transition name="fade|modal|drop">`、`<TransitionGroup name="toast">`、`<Transition name="page">` 已预置。
- 全局已处理 `prefers-reduced-motion` 降级（含环境光斑停动）。JS 动画（如 AnimatedNumber）也已降级。

## 6. 壳层行为（已实现，视图无需关心）

- **App.vue**：环境光背景（3 个漂移光斑）、AppHeader（品牌/主题三段/窗口控制）、AppNav（7 项导航，
  折叠存 `localStorage.tavilyNavCollapsed`，活跃指示条位移）、`<router-view>` page 过渡、
  `<GToast>`、`<LoginModal>`、`<ResizeHandles>`。
- **pywebview 集成**（`src/utils/webview.ts`）：等 `pywebviewready` 后接线，`body.webview` 类；
  Header 空白处拖动（350ms 双击防抖）/双击最大化；8 方向缩放热区（`data-dir` ∈
  top/bottom/left/right/top-left/top-right/bottom-left/bottom-right，与后端 `_HT_HITS` 一一对应）；
  浏览器模式点窗口控制按钮 toast「仅桌面版可用」。
- **401 鉴权门**：任何请求 401 → 挂起 → 登录模态（不可跳过）→ `probeToken` 验证通过 → 自动重试。

## 7. 验收前自查清单

1. `npm run build` 通过（含 vue-tsc 严格模式，`noUnusedLocals` 已开——未用变量会报错）。
2. 视图中所有颜色/圆角/动效时长走 CSS 变量；组件优先复用 §4 清单。
3. 不写死 `http://` 绝对地址（API 全走 client 相对路径）。
4. 新增依赖：**禁止**（离线 + 并行纪律）。确实需要时在结果里报告。
