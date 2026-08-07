<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import AppNav from '@/components/AppNav.vue'
import GButton from '@/components/GButton.vue'
import GModal from '@/components/GModal.vue'
import GToast from '@/components/GToast.vue'
import LoginModal from '@/components/LoginModal.vue'
import ResizeHandles from '@/components/ResizeHandles.vue'
import { getUpdateAnnouncement, type UpdateAnnouncement } from '@/api/client'
import { initAuth } from '@/composables/useAuth'
import { setupWebviewBridge } from '@/utils/webview'

/* App.vue —— 应用壳：环境光背景 / Header / 导航轨 / 视图出口 / Toast / 登录模态 / 更新公告 */

// ── 导航折叠（持久化，键与旧版一致） ──
const NAV_KEY = 'tavilyNavCollapsed'
const navCollapsed = ref(false)
try {
  navCollapsed.value = localStorage.getItem(NAV_KEY) === '1'
} catch { /* 忽略 */ }

function toggleNav(): void {
  navCollapsed.value = !navCollapsed.value
  try {
    localStorage.setItem(NAV_KEY, navCollapsed.value ? '1' : '0')
  } catch { /* 忽略 */ }
}

// ── 更新公告（自动更新/手动更新完成后，新版本首次启动展示本次更新说明）──
const announcementOpen = ref(false)
const announcement = ref<UpdateAnnouncement | null>(null)

async function loadAnnouncement(): Promise<void> {
  try {
    const r = await getUpdateAnnouncement()
    if (r.announcement) {
      announcement.value = r.announcement
      announcementOpen.value = true
    }
  } catch { /* 静默：公告读取失败不打扰 */ }
}

onMounted(() => {
  initAuth()
  setupWebviewBridge()
  loadAnnouncement()
})
</script>

<template>
  <!-- 环境光背景层（缓慢漂移的径向渐变光斑） -->
  <div class="ambient" aria-hidden="true">
    <div class="orb orb-1" />
    <div class="orb orb-2" />
    <div class="orb orb-3" />
  </div>

  <AppHeader />
  <AppNav :collapsed="navCollapsed" @toggle="toggleNav" />

  <main class="app-main" :class="{ 'nav-collapsed': navCollapsed }">
    <RouterView v-slot="{ Component }">
      <Transition name="page" mode="out-in">
        <component :is="Component" />
      </Transition>
    </RouterView>
  </main>

  <ResizeHandles />
  <GToast />
  <LoginModal />

  <!-- 更新公告：更新完成后首次启动展示 -->
  <GModal v-model:open="announcementOpen" title="更新完成公告" width="520px">
    <div class="announcement">
      <p class="announcement-ver">
        已更新到 <b class="u-mono">{{ announcement?.version || '新版本' }}</b>
      </p>
      <pre v-if="announcement?.body" class="announcement-body">{{ announcement.body }}</pre>
      <p v-else class="u-dim">本次更新没有附带更新说明。</p>
    </div>
    <template #footer>
      <GButton size="sm" variant="primary" @click="announcementOpen = false">知道了</GButton>
    </template>
  </GModal>
</template>

<style scoped>
/* ── 环境光背景 ── */
.ambient {
  position: fixed;
  inset: 0;
  z-index: -1;
  overflow: hidden;
  background: var(--bg);
  transition: background var(--dur-2) ease;
}
.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  will-change: transform;
  pointer-events: none;
}
.orb-1 {
  width: 46vw;
  height: 46vw;
  min-width: 480px;
  min-height: 480px;
  top: -18%;
  left: -8%;
  background: radial-gradient(circle, var(--orb-1) 0%, transparent 68%);
  animation: drift-1 26s ease-in-out infinite alternate;
}
.orb-2 {
  width: 38vw;
  height: 38vw;
  min-width: 400px;
  min-height: 400px;
  bottom: -16%;
  right: -6%;
  background: radial-gradient(circle, var(--orb-2) 0%, transparent 68%);
  animation: drift-2 34s ease-in-out infinite alternate;
}
.orb-3 {
  width: 30vw;
  height: 30vw;
  min-width: 320px;
  min-height: 320px;
  top: 30%;
  left: 44%;
  background: radial-gradient(circle, var(--orb-3) 0%, transparent 68%);
  animation: drift-3 40s ease-in-out infinite alternate;
}

@keyframes drift-1 {
  from { transform: translate(0, 0) scale(1); }
  to { transform: translate(7vw, 5vh) scale(1.12); }
}
@keyframes drift-2 {
  from { transform: translate(0, 0) scale(1.05); }
  to { transform: translate(-6vw, -6vh) scale(.95); }
}
@keyframes drift-3 {
  from { transform: translate(0, 0) scale(.95); }
  to { transform: translate(-5vw, 7vh) scale(1.1); }
}

/* ── 主内容区 ── */
.app-main {
  position: fixed;
  top: var(--header-h);
  left: var(--nav-w);
  right: 0;
  bottom: 0;
  overflow-y: auto;
  overflow-x: hidden;
  transition: left var(--dur-2) var(--ease-out);
}
.app-main.nav-collapsed { left: var(--nav-w-collapsed); }
</style>

<!-- 更新公告（GModal Teleport 到 body，scoped 样式不生效，需全局） -->
<style>
.announcement-ver { font-size: 13px; margin: 0 0 8px; }
.announcement-body {
  max-height: 300px;
  overflow: auto;
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-2);
  background: var(--bg-2);
  border: 1px solid var(--glass-border);
  border-radius: 8px;
  padding: 10px 12px;
}
</style>
