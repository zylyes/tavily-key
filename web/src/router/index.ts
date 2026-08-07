import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'

/* ═══════════════════════════════════════════════════════════════
   路由 —— 7 个业务视图，全部懒加载。
   hash 模式：FastAPI 只托管 index（/），深链刷新不依赖服务端重写。
   ═══════════════════════════════════════════════════════════════ */

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/keys' },
  {
    path: '/keys',
    name: 'keys',
    component: () => import('@/views/KeysView.vue'),
    meta: { title: 'API Key 列表' },
  },
  {
    path: '/stats',
    name: 'stats',
    component: () => import('@/views/StatsView.vue'),
    meta: { title: '统计' },
  },
  {
    path: '/logs',
    name: 'logs',
    component: () => import('@/views/LogsView.vue'),
    meta: { title: '请求日志' },
  },
  {
    path: '/mcp',
    name: 'mcp',
    component: () => import('@/views/McpView.vue'),
    meta: { title: 'MCP 服务' },
  },
  {
    path: '/proxy',
    name: 'proxy',
    component: () => import('@/views/ProxyView.vue'),
    meta: { title: '搜索代理' },
  },
  {
    path: '/tasks',
    name: 'tasks',
    component: () => import('@/views/TasksView.vue'),
    meta: { title: 'Research 任务' },
  },
  {
    path: '/docs',
    name: 'docs',
    component: () => import('@/views/DocView.vue'),
    meta: { title: '文档' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
    meta: { title: '设置' },
  },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.afterEach((to) => {
  const t = to.meta.title as string | undefined
  document.title = t ? `${t} · Tavily Key Pool` : 'Tavily Key Pool'
})
