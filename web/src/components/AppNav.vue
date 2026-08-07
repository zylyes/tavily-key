<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import GIcon from './GIcon.vue'

/* AppNav —— 左侧玻璃导航轨（7 项，可折叠为纯图标，活跃指示条位移动画）
   折叠状态由 App.vue 持有并持久化 localStorage.tavilyNavCollapsed。 */

defineProps<{
  collapsed: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle'): void
}>()

interface NavItem {
  path: string
  label: string
  icon: string
}

const ITEMS: NavItem[] = [
  { path: '/keys', label: 'API Key 列表', icon: 'key' },
  { path: '/stats', label: '统计', icon: 'chart' },
  { path: '/logs', label: '请求日志', icon: 'list' },
  { path: '/mcp', label: 'MCP 服务', icon: 'server' },
  { path: '/proxy', label: '搜索代理', icon: 'globe' },
  { path: '/tasks', label: 'Research 任务', icon: 'beaker' },
  { path: '/docs', label: '文档', icon: 'book' },
  { path: '/settings', label: '设置', icon: 'settings' },
]

const route = useRoute()
const activeIndex = computed(() => {
  const i = ITEMS.findIndex((it) => route.path.startsWith(it.path))
  return i < 0 ? 0 : i
})

const indicatorStyle = computed(() => ({
  transform: `translateY(${activeIndex.value * 42}px)`,
  opacity: activeIndex.value < 0 ? '0' : '1',
}))
</script>

<template>
  <nav class="app-nav" :class="{ collapsed }" aria-label="主导航">
    <div class="nav-list">
      <span class="nav-indicator" :style="indicatorStyle" aria-hidden="true" />
      <RouterLink
        v-for="item in ITEMS"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: route.path.startsWith(item.path) }"
        :title="collapsed ? item.label : undefined"
      >
        <GIcon :name="item.icon" :size="17" class="nav-icon" />
        <span class="nav-label u-ellipsis">{{ item.label }}</span>
      </RouterLink>
    </div>

    <button
      type="button"
      class="nav-collapse"
      :aria-expanded="collapsed ? 'false' : 'true'"
      :aria-label="collapsed ? '展开侧边栏' : '折叠侧边栏'"
      :title="collapsed ? '展开侧边栏' : '折叠侧边栏'"
      @click="emit('toggle')"
    >
      <GIcon :name="collapsed ? 'expand' : 'collapse'" :size="15" />
      <span class="nav-label u-ellipsis">折叠导航</span>
    </button>
  </nav>
</template>

<style scoped>
.app-nav {
  position: fixed;
  top: var(--header-h);
  left: 0;
  bottom: 0;
  z-index: var(--z-nav);
  width: var(--nav-w);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 14px 10px 12px;
  background:
    linear-gradient(180deg, var(--glass-hi) 0%, transparent 160px),
    var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border-right: 1px solid var(--glass-border);
  transition: width var(--dur-2) var(--ease-out);
  overflow: hidden;
}
.app-nav.collapsed { width: var(--nav-w-collapsed); }

.nav-list { position: relative; display: flex; flex-direction: column; gap: 4px; }

/* 活跃指示条：位移动画 */
.nav-indicator {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 38px;
  border-radius: var(--r-ctrl);
  background: var(--accent-grad-soft);
  border: 1px solid var(--accent-soft);
  transition: transform var(--dur-3) var(--ease-spring), opacity var(--dur-2) ease;
  pointer-events: none;
}
.nav-indicator::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: var(--r-pill);
  background: var(--accent-grad);
  box-shadow: 0 0 8px var(--accent-soft);
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 38px;
  padding: 0 12px;
  border-radius: var(--r-ctrl);
  color: var(--text-2);
  font-size: 12.5px;
  font-weight: 500;
  text-decoration: none !important;
  transition: color var(--dur-1) ease, background var(--dur-1) ease;
  white-space: nowrap;
}
.nav-item:hover { color: var(--text); background: var(--neutral-soft); }
.nav-item.active { color: var(--text); }
.nav-item.active .nav-icon { color: var(--accent-text); }

.nav-icon { flex: none; }
.nav-label {
  opacity: 1;
  transition: opacity var(--dur-1) ease;
}
.collapsed .nav-label {
  opacity: 0;
  width: 0;
  overflow: hidden;
}
.collapsed .nav-item { justify-content: center; padding: 0; }

.nav-collapse {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 34px;
  padding: 0 12px;
  border-radius: var(--r-ctrl);
  color: var(--text-3);
  font-size: 12px;
  transition: color var(--dur-1) ease, background var(--dur-1) ease;
  white-space: nowrap;
}
.nav-collapse:hover { color: var(--text); background: var(--neutral-soft); }
.collapsed .nav-collapse { justify-content: center; padding: 0; }
</style>
