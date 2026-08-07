<script setup lang="ts">
/* SettingsView —— 设置：通用（开机自启/托盘行为）、部署、数据备份与恢复
   对齐旧版 dashboard.html settings panel（loadSettings / saveSettings /
   saveGeneralSettings / backupData / restoreData） */
import { computed, onMounted, reactive, ref, type Ref } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import GlassCard from '@/components/GlassCard.vue'
import GButton from '@/components/GButton.vue'
import GIcon from '@/components/GIcon.vue'
import GInput from '@/components/GInput.vue'
import GSelect from '@/components/GSelect.vue'
import GSwitch from '@/components/GSwitch.vue'
import GModal from '@/components/GModal.vue'
import Skeleton from '@/components/Skeleton.vue'
import { useToast } from '@/composables/useToast'
import {
  ApiError,
  backupData,
  getAutostart,
  getSettings,
  restoreData,
  saveBlob,
  saveSettings,
  setAutostart,
  type Settings,
  type SettingsPatch,
} from '@/api/client'
import SettingRow from './parts/settings/SettingRow.vue'
import { saveBackupAs } from '@/utils/webview'

const toast = useToast()

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

const loading = ref(true)
const publicUrl = ref('')

// ── 通用设置：开机自启（注册表为准）+ 窗口托盘行为，切换即存 ──
const autostart = ref(false)
const autostartBusy = ref(false)
const startToTray = ref(false)
const closeToTray = ref(false)
const minimizeToTray = ref(false)
const traySaving = ref(false)

async function onAutostartChange(v: boolean): Promise<void> {
  if (autostartBusy.value) return
  autostartBusy.value = true
  try {
    const r = await setAutostart(v)
    autostart.value = r.enabled
    toast.success(r.enabled ? '已开启开机自启' : '已关闭开机自启')
  } catch (e) {
    autostart.value = !v
    toast.error(`开机自启设置失败：${errMsg(e)}`)
  } finally {
    autostartBusy.value = false
  }
}

type TrayField = 'start_to_tray' | 'close_to_tray' | 'minimize_to_tray'

const trayRefs: Record<TrayField, Ref<boolean>> = {
  start_to_tray: startToTray,
  close_to_tray: closeToTray,
  minimize_to_tray: minimizeToTray,
}

async function onTrayChange(field: TrayField, v: boolean): Promise<void> {
  const target = trayRefs[field]
  if (traySaving.value) {
    target.value = !v
    return
  }
  traySaving.value = true
  try {
    await saveSettings({ [field]: v })
    toast.success('通用设置已保存')
  } catch (e) {
    target.value = !v
    toast.error(`通用设置保存失败：${errMsg(e)}`)
  } finally {
    traySaving.value = false
  }
}

// ── 部署设置：收集 dirty 字段一次保存 ────────────────────────
const deploy = reactive({
  mode: 'local',
  domain: '',
  host: '',
  port: '8000',
  auth_token: '',
})
const deploySnap = ref({ ...deploy })
const deploySaving = ref(false)
const showToken = ref(false)

const MODE_OPTIONS = [
  { label: 'Server（Linux 服务器 · 域名对外服务）', value: 'server' },
  { label: 'Local（Windows 本地 · 本机提供服务）', value: 'local' },
]

const portValid = computed(() => {
  const s = deploy.port.trim()
  if (!/^\d+$/.test(s)) return false
  const n = Number(s)
  return n >= 1 && n <= 65535
})

const deployDirty = computed(
  () =>
    deploy.mode !== deploySnap.value.mode ||
    deploy.domain.trim() !== deploySnap.value.domain ||
    deploy.host.trim() !== deploySnap.value.host ||
    deploy.port.trim() !== deploySnap.value.port ||
    deploy.auth_token !== deploySnap.value.auth_token,
)

function applySettings(s: Settings): void {
  deploy.mode = s.mode
  deploy.domain = s.domain || ''
  deploy.host = s.host || ''
  deploy.port = String(s.port ?? 8000)
  deploy.auth_token = s.auth_token || ''
  startToTray.value = !!s.start_to_tray
  closeToTray.value = !!s.close_to_tray
  minimizeToTray.value = !!s.minimize_to_tray
  deploySnap.value = { ...deploy }
}

async function saveDeploy(): Promise<void> {
  if (deploySaving.value) return
  if (!portValid.value) {
    toast.error('端口需为 1–65535 的整数')
    return
  }
  const snap = deploySnap.value
  const patch: SettingsPatch = {}
  if (deploy.mode !== snap.mode) patch.mode = deploy.mode === 'server' ? 'server' : 'local'
  if (deploy.domain.trim() !== snap.domain) patch.domain = deploy.domain.trim()
  if (deploy.host.trim() !== snap.host) patch.host = deploy.host.trim()
  if (deploy.port.trim() !== snap.port) patch.port = Number(deploy.port.trim())
  if (deploy.auth_token !== snap.auth_token) patch.auth_token = deploy.auth_token.trim()
  if (!Object.keys(patch).length) {
    toast.info('没有需要保存的改动')
    return
  }
  deploySaving.value = true
  try {
    const r = await saveSettings(patch)
    applySettings(r.settings)
    publicUrl.value = r.public_url
    toast.success('设置已保存')
  } catch (e) {
    // 400（validate_patch 拒绝）时直接展示后端错误文案
    toast.error(e instanceof ApiError ? `保存失败：${e.message}` : `设置保存失败：${errMsg(e)}`)
  } finally {
    deploySaving.value = false
  }
}

// ── 数据备份与恢复 ──────────────────────────────────────────
const backupBusy = ref(false)

async function doBackup(): Promise<void> {
  if (backupBusy.value) return
  backupBusy.value = true
  try {
    // 桌面版：系统「另存为」对话框（默认程序根目录），后端直接写入所选位置
    if (window.pywebview?.api?.save_backup_as) {
      const r = await saveBackupAs()
      if (r?.cancelled) return // 用户取消：不提示
      if (!r?.ok) throw new Error(r?.error || '备份失败')
      toast.success(`备份已保存：${r.path}`)
      return
    }
    // 浏览器模式：生成备份 blob，优先让用户选择保存位置，不支持时降级 <a download>
    const { blob, filename } = await backupData()
    if (window.showSaveFilePicker) {
      const handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description: 'ZIP 备份', accept: { 'application/zip': ['.zip'] } }],
      })
      const writable = await handle.createWritable()
      await writable.write(blob)
      await writable.close()
    } else {
      saveBlob(blob, filename)
    }
    toast.success('备份已下载')
  } catch (e) {
    // 用户取消「另存为」对话框（AbortError）不视为错误
    if (e instanceof DOMException && e.name === 'AbortError') return
    toast.error(`备份失败：${errMsg(e)}`)
  } finally {
    backupBusy.value = false
  }
}

const restoreInput = ref<HTMLInputElement | null>(null)
const restoreFile = ref<File | null>(null)
const restoreConfirm = ref(false)
const restoreBusy = ref(false)

function pickRestoreFile(): void {
  restoreInput.value?.click()
}
function onRestoreFileChange(ev: Event): void {
  restoreFile.value = (ev.target as HTMLInputElement).files?.[0] ?? null
}
function clearRestoreFile(): void {
  restoreFile.value = null
  if (restoreInput.value) restoreInput.value.value = ''
}
async function doRestore(): Promise<void> {
  if (!restoreFile.value || restoreBusy.value) return
  restoreBusy.value = true
  try {
    const r = await restoreData(restoreFile.value)
    restoreConfirm.value = false
    clearRestoreFile()
    toast.success(`已恢复 ${r.restored} 个文件，请手动重启 MCP / 搜索代理服务`, 6000)
  } catch (e) {
    toast.error(`恢复失败：${errMsg(e)}`)
  } finally {
    restoreBusy.value = false
  }
}

// ── 初始加载 ────────────────────────────────────────────────
onMounted(async () => {
  try {
    // getAutostart 在非 Windows 部署可能不可用，独立容错
    const [s, a] = await Promise.all([getSettings(), getAutostart().catch(() => null)])
    applySettings(s.settings)
    publicUrl.value = s.public_url
    if (a) autostart.value = a.enabled
  } catch (e) {
    toast.error(`设置加载失败：${errMsg(e)}`)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="view">
    <PageHeader title="设置" desc="窗口行为、部署监听与访问鉴权、数据备份与恢复" />

    <template v-if="loading">
      <GlassCard><Skeleton :lines="4" /></GlassCard>
      <GlassCard><Skeleton :lines="5" /></GlassCard>
      <GlassCard><Skeleton :lines="3" /></GlassCard>
    </template>

    <div v-else class="stagger settings-list">
      <!-- 通用设置 -->
      <GlassCard title="通用设置" desc="开机自启与窗口行为（关闭 / 最小化到系统托盘）">
        <SettingRow label="开机自启" hint="登录 Windows 后自动启动（以系统注册表为准）">
          <GSwitch v-model="autostart" :disabled="autostartBusy" @change="onAutostartChange" />
        </SettingRow>
        <SettingRow label="启动程序时" hint="开启：直接最小化到系统托盘（适合开机自启）">
          <GSwitch
            v-model="startToTray"
            :disabled="traySaving"
            @change="(v) => onTrayChange('start_to_tray', v)"
          />
        </SettingRow>
        <SettingRow label="点击关闭按钮时" hint="开启：最小化到系统托盘，后台继续运行">
          <GSwitch
            v-model="closeToTray"
            :disabled="traySaving"
            @change="(v) => onTrayChange('close_to_tray', v)"
          />
        </SettingRow>
        <SettingRow label="点击最小化按钮时" hint="开启：隐藏到系统托盘；关闭：最小化到任务栏">
          <GSwitch
            v-model="minimizeToTray"
            :disabled="traySaving"
            @change="(v) => onTrayChange('minimize_to_tray', v)"
          />
        </SettingRow>
      </GlassCard>

      <!-- 部署设置 -->
      <GlassCard title="部署设置" desc="配置服务的部署模式、监听地址与访问鉴权">
        <SettingRow label="部署模式">
          <GSelect
            :model-value="deploy.mode"
            :options="MODE_OPTIONS"
            class="ctrl-full"
            @update:model-value="(v) => (deploy.mode = String(v))"
          />
        </SettingRow>
        <SettingRow label="绑定域名" hint="仅界面展示用途">
          <GInput v-model="deploy.domain" mono placeholder="例如 api.example.com" />
        </SettingRow>
        <SettingRow label="监听地址 Host" hint="127.0.0.1 仅本机 / 0.0.0.0 所有网卡">
          <GInput v-model="deploy.host" mono placeholder="127.0.0.1" />
        </SettingRow>
        <SettingRow
          label="端口"
          :hint="portValid ? 'Dashboard / MCP / 搜索代理服务监听端口' : '端口需为 1–65535 的整数'"
        >
          <GInput v-model="deploy.port" type="number" mono placeholder="8000" />
        </SettingRow>
        <SettingRow label="访问令牌" hint="留空则不鉴权；设置后所有 /api/* 请求需携带令牌">
          <GInput
            v-model="deploy.auth_token"
            :type="showToken ? 'text' : 'password'"
            mono
            autocomplete="off"
            placeholder="访问令牌"
          />
          <GButton
            size="sm"
            text
            :aria-label="showToken ? '隐藏令牌' : '显示令牌'"
            @click="showToken = !showToken"
          >
            <GIcon :name="showToken ? 'eyeOff' : 'eye'" :size="14" />
          </GButton>
        </SettingRow>
        <div class="deploy-actions">
          <GButton
            variant="primary"
            size="sm"
            :busy="deploySaving"
            :disabled="!deployDirty"
            @click="saveDeploy"
          >
            <GIcon name="check" :size="13" />保存设置
          </GButton>
          <span v-if="publicUrl" class="u-dim deploy-url">
            对外访问地址：<span class="u-mono">{{ publicUrl }}</span>
          </span>
        </div>
      </GlassCard>

      <!-- 数据备份与恢复 -->
      <GlassCard
        title="数据备份与恢复"
        desc="备份/恢复 data/ 目录（配置、Key 池与加密密钥、research 缓存）。恢复会先停止 MCP 与搜索代理服务，完成后请手动重启。"
      >
        <SettingRow label="备份" hint="下载 data/ 目录完整备份（zip）">
          <GButton size="sm" variant="primary" :busy="backupBusy" @click="doBackup">
            <GIcon name="download" :size="13" />下载备份
          </GButton>
        </SettingRow>
        <SettingRow label="恢复" hint="选择备份 zip，确认后覆盖当前数据（危险操作）">
          <input
            ref="restoreInput"
            type="file"
            accept=".zip,application/zip"
            class="visually-hidden"
            @change="onRestoreFileChange"
          />
          <GButton size="sm" @click="pickRestoreFile">
            <GIcon name="upload" :size="13" />选择文件
          </GButton>
          <span v-if="restoreFile" class="u-muted u-ellipsis restore-name">{{
            restoreFile.name
          }}</span>
          <GButton
            size="sm"
            variant="danger"
            :disabled="!restoreFile"
            @click="restoreConfirm = true"
          >
            恢复备份
          </GButton>
        </SettingRow>
      </GlassCard>
    </div>

    <!-- 恢复强确认 -->
    <GModal v-model:open="restoreConfirm" title="恢复备份" width="480px">
      <div class="restore-confirm">
        <p>
          即将从 <b class="u-mono">{{ restoreFile?.name }}</b> 恢复数据：
        </p>
        <ul>
          <li>当前配置与 Key 池将被覆盖（现有文件会先保留为 .pre-restore 副本）</li>
          <li>MCP 与搜索代理服务会被停止，完成后需手动重启</li>
        </ul>
      </div>
      <template #footer>
        <GButton size="sm" @click="restoreConfirm = false">取消</GButton>
        <GButton size="sm" variant="danger" :busy="restoreBusy" @click="doRestore">
          确认恢复
        </GButton>
      </template>
    </GModal>
  </div>
</template>

<style scoped>
.settings-list { display: flex; flex-direction: column; gap: 16px; }

.ctrl-full { width: 100%; }

.deploy-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--glass-border);
}
.deploy-url { font-size: 11px; }

.restore-name { max-width: 110px; font-size: 11px; }

.restore-confirm p { font-size: 12.5px; }
.restore-confirm ul {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.8;
  color: var(--text-2);
}
</style>
