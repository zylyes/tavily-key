<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import AppNav from '@/components/AppNav.vue'
import GButton from '@/components/GButton.vue'
import GIcon from '@/components/GIcon.vue'
import GModal from '@/components/GModal.vue'
import GToast from '@/components/GToast.vue'
import LoginModal from '@/components/LoginModal.vue'
import ResizeHandles from '@/components/ResizeHandles.vue'
import { getUpdateAnnouncement, type UpdateAnnouncement } from '@/api/client'
import MdView from '@/components/MdView.vue'
import { initAuth } from '@/composables/useAuth'
import { setupWebviewBridge } from '@/utils/webview'
import {
  cancelDownload,
  closeMini,
  doApplyUpdate,
  fmtBytes,
  minimizeNotice,
  noticeOpen,
  miniOpen,
  restoreNotice,
  retryDownload,
  startDownload,
  togglePause,
  updateDl,
  updateFlow,
  updatePct,
  versionType,
  versionTypeLabel,
  openRelease,
  update,
  initUpdateNotice,
} from '@/composables/useUpdateNotice'

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
  initUpdateNotice()
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
      <MdView v-if="announcement?.body" :text="announcement.body" class="announcement-body" />
      <p v-else class="u-dim">本次更新没有附带更新说明。</p>
    </div>
    <template #footer>
      <GButton size="sm" variant="primary" @click="announcementOpen = false">知道了</GButton>
    </template>
  </GModal>

  <!-- 更新公告弹窗（新版本 release notes；点击「立即更新」直接下载并在下方显示进度） -->
  <GModal v-model:open="noticeOpen" title="更新公告" width="600px">
    <template #head-actions>
      <button class="announce-min" aria-label="最小化到右下角通知" @click="minimizeNotice">
        <GIcon name="minimize" :size="13" />
      </button>
    </template>
    <div class="announce-dialog">
      <div class="announce-versions">
        <span class="u-mono announce-ver">{{ update?.current_version ?? '—' }}</span>
        <span class="announce-arrow">→</span>
        <span class="u-mono announce-ver announce-ver-new">{{ update?.latest_version ?? '' }}</span>
        <span class="announce-tag" :class="versionType === 'beta' ? 'is-beta' : ''">
          {{ versionTypeLabel }}
        </span>
      </div>
      <div class="announce-body">
        <MdView v-if="update?.body" :text="update.body" />
        <p v-else class="u-dim">无更新说明，可前往 GitHub 查看发布页</p>
      </div>

      <!-- 下载进度（公告下方） -->
      <template
        v-if="updateFlow === 'downloading' || updateFlow === 'ready' || updateFlow === 'error'"
      >
        <div v-if="updateFlow === 'downloading'" class="announce-progress">
          <div class="announce-progress-head">
            <span class="announce-progress-title">
              {{ updateDl?.state === 'paused' ? '下载已暂停' : '正在下载更新包' }}
            </span>
            <span class="u-mono u-dim announce-progress-pct">
              {{ updatePct() >= 0 ? `${updatePct()}%` : '…' }}
            </span>
          </div>
          <div class="update-progress announce-progress-bar">
            <div
              class="update-progress-fill"
              :style="{ width: updatePct() >= 0 ? updatePct() + '%' : '8%' }"
            />
          </div>
          <div class="announce-progress-meta">
            <span class="u-mono u-dim">
              {{ fmtBytes(updateDl?.received ?? 0) }} / {{ fmtBytes(updateDl?.total ?? 0) }}
            </span>
            <span v-if="updateDl?.state === 'paused'" class="u-dim">
              已暂停，点击「继续」恢复下载
            </span>
          </div>
        </div>
        <div v-else-if="updateFlow === 'ready'" class="announce-progress announce-progress-done">
          <GIcon name="check" :size="15" />
          <span>
            新版 <b class="u-mono">{{ updateDl?.version || '' }}</b> 已下载完成，
            点击「重启应用」完成更新
          </span>
        </div>
        <div v-else-if="updateFlow === 'error'" class="announce-progress announce-progress-error">
          <GIcon name="alert" :size="15" />
          <span>
            下载失败：{{ updateDl?.error || '未知错误' }}，可重试或前往 GitHub 手动下载
          </span>
        </div>
      </template>
    </div>
    <template #footer>
      <!-- 下载中 / 已暂停：暂停/继续 + 取消 -->
      <template v-if="updateFlow === 'downloading'">
        <GButton size="sm" @click="togglePause">
          <GIcon :name="updateDl?.state === 'paused' ? 'play' : 'pause'" :size="13" />
          {{ updateDl?.state === 'paused' ? '继续' : '暂停' }}
        </GButton>
        <GButton size="sm" variant="danger" @click="cancelDownload">
          <GIcon name="x" :size="13" />取消下载
        </GButton>
      </template>
      <!-- 下载完成：重启应用 -->
      <template v-else-if="updateFlow === 'ready' || updateFlow === 'applying'">
        <GButton
          size="sm"
          variant="primary"
          :busy="updateFlow === 'applying'"
          @click="doApplyUpdate"
        >
          <GIcon name="refresh" :size="13" />重启应用
        </GButton>
      </template>
      <!-- 下载失败：重试 / 关闭 -->
      <template v-else-if="updateFlow === 'error'">
        <GButton size="sm" variant="primary" @click="retryDownload">
          <GIcon name="refresh" :size="13" />重试
        </GButton>
        <GButton size="sm" @click="noticeOpen = false">关闭</GButton>
      </template>
      <!-- 默认：前往 GitHub + 立即更新 -->
      <template v-else>
        <GButton size="sm" @click="openRelease(update?.release_url || '')">
          <GIcon name="external" :size="13" />前往 GitHub
        </GButton>
        <GButton
          v-if="update?.can_auto_update"
          size="sm"
          variant="primary"
          @click="startDownload"
        >
          <GIcon name="download" :size="13" />立即更新
        </GButton>
        <GButton v-else size="sm" variant="primary" @click="noticeOpen = false">知道了</GButton>
      </template>
    </template>
  </GModal>

  <!-- 更新公告最小化通知（右下角悬浮，类似通知；下载中显示进度） -->
  <Teleport to="body">
    <div v-if="miniOpen" class="mini-notice" role="status" aria-live="polite">
      <div class="mini-notice-head">
        <span class="mini-notice-title">
          <GIcon name="zap" :size="13" />更新公告
        </span>
        <button class="mini-notice-x" aria-label="关闭通知" @click="closeMini">
          <GIcon name="x" :size="12" />
        </button>
      </div>
      <button class="mini-notice-body" @click="restoreNotice">
        <div class="mini-notice-versions">
          <span class="u-mono mini-ver">{{ update?.current_version ?? '—' }}</span>
          <span class="mini-arrow">→</span>
          <span class="u-mono mini-ver mini-ver-new">{{ update?.latest_version ?? '' }}</span>
          <span class="mini-tag" :class="versionType === 'beta' ? 'is-beta' : ''">
            {{ versionTypeLabel }}
          </span>
        </div>
        <!-- 下载中 / 已暂停：进度条 -->
        <template v-if="updateFlow === 'downloading'">
          <div class="mini-progress">
            <div class="mini-progress-bar">
              <div
                class="mini-progress-fill"
                :style="{ width: updatePct() >= 0 ? updatePct() + '%' : '8%' }"
              />
            </div>
            <span class="u-mono mini-pct">
              {{ updatePct() >= 0 ? `${updatePct()}%` : '…' }}
            </span>
          </div>
          <div class="mini-meta">
            <span class="u-mono u-dim">
              {{ fmtBytes(updateDl?.received ?? 0) }} / {{ fmtBytes(updateDl?.total ?? 0) }}
            </span>
            <span v-if="updateDl?.state === 'paused'" class="u-dim">已暂停</span>
          </div>
        </template>
        <!-- 下载完成 -->
        <div v-else-if="updateFlow === 'ready'" class="mini-done">
          <GIcon name="check" :size="14" />
          <span>新版 {{ updateDl?.version || '' }} 已下载完成</span>
        </div>
        <!-- 下载失败 -->
        <div v-else-if="updateFlow === 'error'" class="mini-error">
          <GIcon name="alert" :size="14" />
          <span>下载失败，可重试</span>
        </div>
        <!-- 默认：有新版本 -->
        <div v-else class="mini-idle">
          发现新版本 {{ update?.latest_version ?? '' }}，点击查看公告
        </div>
      </button>
      <!-- 操作按钮（下载中 / 完成 / 失败） -->
      <div
        v-if="updateFlow === 'downloading' || updateFlow === 'ready' || updateFlow === 'error'"
        class="mini-actions"
      >
        <template v-if="updateFlow === 'downloading'">
          <GButton size="sm" text @click.stop="togglePause">
            <GIcon :name="updateDl?.state === 'paused' ? 'play' : 'pause'" :size="12" />
            {{ updateDl?.state === 'paused' ? '继续' : '暂停' }}
          </GButton>
          <GButton size="sm" text @click.stop="cancelDownload">
            <GIcon name="x" :size="12" />取消
          </GButton>
        </template>
        <GButton
          v-else-if="updateFlow === 'ready'"
          size="sm"
          variant="primary"
          @click.stop="doApplyUpdate"
        >
          <GIcon name="refresh" :size="12" />重启应用
        </GButton>
        <GButton v-else-if="updateFlow === 'error'" size="sm" text @click.stop="retryDownload">
          <GIcon name="refresh" :size="12" />重试
        </GButton>
      </div>
    </div>
  </Teleport>
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
  background:
    linear-gradient(180deg, var(--glass-hi) 0%, transparent 120px),
    var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--r-ctrl);
  padding: 10px 12px;
}

/* ── 更新公告弹窗（GModal Teleport 到 body，需全局）── */
.announce-dialog { display: flex; flex-direction: column; gap: 12px; }
.announce-versions {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.announce-ver { color: var(--text); }
.announce-ver-new { color: var(--accent-text); font-weight: 650; }
.announce-arrow { color: var(--text-3); }
.announce-tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  font-size: 10.5px;
  line-height: 1.5;
  border-radius: var(--r-pill);
  color: var(--accent-text);
  background: var(--accent-soft);
  border: 1px solid var(--accent-softer);
}
.announce-tag.is-beta {
  color: var(--warn);
  background: var(--warn-soft);
  border-color: transparent;
}
.announce-body {
  max-height: 46vh;
  overflow: auto;
  background:
    linear-gradient(180deg, var(--glass-hi) 0%, transparent 120px),
    var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--r-ctrl);
  padding: 12px 14px;
}

/* 公告弹窗内：下载进度 / 就绪 / 失败 */
.announce-progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--glass-border);
  border-radius: var(--r-ctrl);
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
}
.announce-progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}
.announce-progress-title { font-weight: 550; }
.announce-progress-pct { font-size: 11px; }
.announce-progress-bar {
  width: 100%;
  height: 8px;
  border-radius: 99px;
  background: var(--bg-3);
  overflow: hidden;
}
.announce-progress-bar .update-progress-fill {
  height: 100%;
  border-radius: 99px;
  background: var(--info);
  transition: width .3s ease;
}
.announce-progress-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
}
.announce-progress-done,
.announce-progress-error {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
}
.announce-progress-done { color: var(--success); }
.announce-progress-error { color: var(--danger); }

/* 公告弹窗标题栏：最小化按钮 */
.announce-min {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--r-sm);
  color: var(--text-3);
  transition: background var(--dur-1) ease, color var(--dur-1) ease;
}
.announce-min:hover { background: var(--neutral-soft); color: var(--text); }

/* 更新公告最小化通知（右下角悬浮，Teleport 到 body，需全局） */
.mini-notice {
  position: fixed;
  right: 18px;
  bottom: 18px;
  z-index: var(--z-toast);
  width: 272px;
  display: flex;
  flex-direction: column;
  background:
    linear-gradient(180deg, var(--glass-hi) 0%, transparent 90px),
    var(--glass-bg-2);
  backdrop-filter: blur(24px) saturate(1.5);
  -webkit-backdrop-filter: blur(24px) saturate(1.5);
  border: 1px solid var(--glass-border-strong);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
  overflow: hidden;
}
.mini-notice-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 10px 0;
}
.mini-notice-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent-text);
}
.mini-notice-x {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: var(--r-sm);
  color: var(--text-3);
  transition: background var(--dur-1) ease, color var(--dur-1) ease;
}
.mini-notice-x:hover { background: var(--neutral-soft); color: var(--text); }
.mini-notice-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 12px 10px;
  text-align: left;
}
.mini-notice-body:hover .mini-notice-versions { opacity: .85; }
.mini-notice-versions {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  transition: opacity var(--dur-1) ease;
}
.mini-ver { color: var(--text); }
.mini-ver-new { color: var(--accent-text); font-weight: 650; }
.mini-arrow { color: var(--text-3); }
.mini-tag {
  display: inline-flex;
  align-items: center;
  padding: 1px 7px;
  font-size: 10px;
  line-height: 1.5;
  border-radius: var(--r-pill);
  color: var(--accent-text);
  background: var(--accent-soft);
  border: 1px solid var(--accent-softer);
}
.mini-tag.is-beta {
  color: var(--warn);
  background: var(--warn-soft);
  border-color: transparent;
}
.mini-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mini-progress-bar {
  flex: 1;
  height: 7px;
  border-radius: 99px;
  background: var(--bg-3);
  overflow: hidden;
}
.mini-progress-fill {
  height: 100%;
  border-radius: 99px;
  background: var(--info);
  transition: width .3s ease;
}
.mini-pct {
  min-width: 36px;
  font-size: 11px;
  text-align: right;
}
.mini-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 10.5px;
}
.mini-done,
.mini-error,
.mini-idle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  line-height: 1.5;
}
.mini-done { color: var(--success); }
.mini-error { color: var(--danger); }
.mini-idle { color: var(--text-2); }
.mini-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  padding: 0 10px 10px;
}
.mini-actions .g-btn { min-height: 26px; padding: 0 10px; font-size: 11.5px; }
</style>
