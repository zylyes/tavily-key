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
  exportAuditZip,
  getAutostart,
  getSettings,
  restoreData,
  saveBlob,
  saveSettings,
  setAutostart,
  type Settings,
  type SettingsPatch,
} from '@/api/client'
import {
  doCheckUpdate,
  openNotice,
  openRelease,
  startDownload,
  update,
  updateBusy,
  updateFlow,
  versionType,
  versionTypeLabel,
} from '@/composables/useUpdateNotice'
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
  autoBackupEnabled.value = !!s.auto_backup_enabled
  autoBackupInterval.value = String(s.auto_backup_interval_days ?? 1)
  autoBackupKeep.value = String(s.auto_backup_keep ?? 7)
  updateEnabled.value = !!s.update_check_enabled
  applyUpdateInterval(
    Number(s.update_check_interval_hours ?? 24),
    String(s.update_check_interval_unit ?? 'hour'),
  )
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

// ── 审计导出（全量请求日志审计包 zip）───────────────────────
const auditBusy = ref(false)

async function doAuditExport(): Promise<void> {
  if (auditBusy.value) return
  auditBusy.value = true
  try {
    const { blob, filename } = await exportAuditZip()
    saveBlob(blob, filename)
    toast.success('审计包已导出')
  } catch (e) {
    toast.error(`审计导出失败：${errMsg(e)}`)
  } finally {
    auditBusy.value = false
  }
}
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

// ── 定时自动备份（默认关闭）──────────────────────────────────
const autoBackupEnabled = ref(false)
const autoBackupInterval = ref('1')
const autoBackupKeep = ref('7')
const autoBackupSaving = ref(false)

async function onAutoBackupToggle(v: boolean): Promise<void> {
  autoBackupEnabled.value = v
  await saveAutoBackup()
}

async function saveAutoBackup(): Promise<void> {
  if (autoBackupSaving.value) return
  const interval = parseInt(autoBackupInterval.value.trim(), 10)
  const keep = parseInt(autoBackupKeep.value.trim(), 10)
  if (!interval || interval < 1 || interval > 365) {
    toast.error('备份间隔需为 1–365 天的整数')
    return
  }
  if (!keep || keep < 1 || keep > 100) {
    toast.error('保留份数需为 1–100 的整数')
    return
  }
  autoBackupSaving.value = true
  try {
    await saveSettings({
      auto_backup_enabled: autoBackupEnabled.value,
      auto_backup_interval_days: interval,
      auto_backup_keep: keep,
    })
    toast.success('自动备份设置已保存')
  } catch (e) {
    toast.error(`自动备份设置保存失败：${errMsg(e)}`)
  } finally {
    autoBackupSaving.value = false
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

// ── 更新检查设置（开关 / 间隔；检查与通知逻辑在 useUpdateNotice 全局共享）──
const updateEnabled = ref(true)
const updateInterval = ref('24')
const updateIntervalUnit = ref<'hour' | 'day' | 'week' | 'month'>('hour')
const updateCfgSaving = ref(false)

/** 单位 → 小时换算（月按 30 天计） */
const UNIT_HOURS: Record<string, number> = { hour: 1, day: 24, week: 168, month: 720 }
/** 检查间隔上限：无论什么单位最多一年（365 天 = 8760 小时） */
const MAX_YEAR_HOURS = 365 * 24
const UNIT_OPTIONS: Array<{ label: string; value: string }> = [
  { label: '小时', value: 'hour' },
  { label: '日', value: 'day' },
  { label: '星期', value: 'week' },
  { label: '月', value: 'month' },
]

/** 把后端小时数按单位换算为面板展示值（无法整除时回退小时单位展示；0=旧版关闭值回退默认 24） */
function applyUpdateInterval(hours: number, unit: string): void {
  const safe = hours > 0 ? hours : 24
  const u = (UNIT_HOURS[unit] ? unit : 'hour') as 'hour' | 'day' | 'week' | 'month'
  const m = UNIT_HOURS[u]
  const v = safe / m
  if (!Number.isInteger(v)) {
    updateIntervalUnit.value = 'hour'
    updateInterval.value = String(safe)
  } else {
    updateIntervalUnit.value = u
    updateInterval.value = String(v)
  }
}

/** 当前输入按单位换算成小时；非法输入返回 -1 */
function updateHoursValue(): number {
  const v = parseFloat(updateInterval.value.trim())
  if (Number.isNaN(v) || v < 0) return -1
  return v * (UNIT_HOURS[updateIntervalUnit.value] ?? 1)
}

/** 切换单位：按原单位换算后保持间隔不变（数值自动换算） */
function onUnitChange(u: string | number): void {
  const next = String(u) as 'hour' | 'day' | 'week' | 'month'
  if (!UNIT_HOURS[next] || next === updateIntervalUnit.value) return
  const v = parseFloat(updateInterval.value.trim())
  if (!Number.isNaN(v) && v >= 0) {
    const hours = v * (UNIT_HOURS[updateIntervalUnit.value] ?? 1)
    const r = hours / UNIT_HOURS[next]
    updateInterval.value = String(Math.round(r * 100) / 100)
  }
  updateIntervalUnit.value = next
}

/** 自动检查开关（update_check_enabled） */
async function onUpdateEnabledChange(v: boolean): Promise<void> {
  updateEnabled.value = v
  await saveUpdateCfg()
}

async function saveUpdateCfg(): Promise<void> {
  if (updateCfgSaving.value) return
  const hours = updateHoursValue()
  // 自动检查由 update_check_enabled 开关控制，间隔不再支持 0；上限为最多一年（8760 小时）
  if (hours < 1 || !Number.isInteger(hours) || hours > MAX_YEAR_HOURS) {
    toast.error('检查间隔需为 1 小时～1 年（8760 小时）的整数，如 1 小时、1 天、1 星期、1 月')
    return
  }
  updateCfgSaving.value = true
  try {
    await saveSettings({
      update_check_enabled: updateEnabled.value,
      update_check_interval_hours: hours,
      update_check_interval_unit: updateIntervalUnit.value,
    })
    toast.success('更新检查设置已保存')
  } catch (e) {
    toast.error(`更新检查设置保存失败：${errMsg(e)}`)
  } finally {
    updateCfgSaving.value = false
  }
}
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
        <SettingRow
          label="审计导出"
          hint="全量请求日志审计包（zip：请求日志 CSV + Key 池状态 + 汇总，不含密钥明文）"
        >
          <GButton size="sm" :busy="auditBusy" @click="doAuditExport">
            <GIcon name="download" :size="13" />导出审计包
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
        <SettingRow
          label="定时自动备份"
          hint="默认关闭。开启后按间隔自动备份到 data/backups/，仅保留最近 N 份"
        >
          <GSwitch
            :model-value="autoBackupEnabled"
            :disabled="autoBackupSaving"
            @change="onAutoBackupToggle"
          />
        </SettingRow>
        <template v-if="autoBackupEnabled">
          <SettingRow class="sub-row" label="备份间隔（天）" hint="每隔多少天自动备份一次">
            <GInput
              v-model="autoBackupInterval"
              type="number"
              class="ctrl-num"
              @change="saveAutoBackup"
            />
          </SettingRow>
          <SettingRow class="sub-row" label="保留份数" hint="超出后自动删除最旧备份">
            <GInput
              v-model="autoBackupKeep"
              type="number"
              class="ctrl-num"
              @change="saveAutoBackup"
            />
          </SettingRow>
        </template>
      </GlassCard>

      <!-- 关于与更新 -->
      <GlassCard title="关于与更新" desc="从 GitHub 检查最新版本，获取功能与修复更新">
        <!-- 1. 检查间隔（关闭自动检查时整行变灰、不可修改） -->
        <SettingRow
          :class="{ 'interval-disabled': !updateEnabled }"
          label="检查间隔"
          :hint="updateEnabled ? '每隔多久自动检查一次' : '自动检查更新已关闭，此设置不生效'"
        >
          <div class="interval-row">
            <GInput
              v-model="updateInterval"
              type="number"
              class="ctrl-num"
              placeholder="24"
              :disabled="updateCfgSaving || !updateEnabled"
              @change="saveUpdateCfg"
            />
            <GSelect
              :model-value="updateIntervalUnit"
              :options="UNIT_OPTIONS"
              size="sm"
              :disabled="updateCfgSaving || !updateEnabled"
              @change="onUnitChange"
            />
          </div>
        </SettingRow>

        <!-- 2. 自动检查更新 -->
        <SettingRow label="自动检查更新" hint="开启后按间隔后台检查新版本，发现时通知">
          <GSwitch
            :model-value="updateEnabled"
            :disabled="updateCfgSaving"
            @change="onUpdateEnabledChange"
          />
        </SettingRow>

        <!-- 3. 当前版本 + 检查更新（融合：左当前版本与类型，右检查更新按钮） -->
        <div class="update-version-row">
          <div class="update-version-info">
            <span class="uv-label">当前版本</span>
            <span class="u-mono update-version">{{ update?.current_version ?? '—' }}</span>
            <span class="version-badge" :class="versionType === 'beta' ? 'is-beta' : ''">
              {{ versionTypeLabel }}
            </span>
            <span v-if="update?.update_available" class="uv-new">
              发现新版本 {{ update.latest_version }}
            </span>
          </div>
          <div class="update-version-actions">
            <GButton size="sm" variant="primary" :busy="updateBusy" @click="doCheckUpdate(true)">
              <GIcon name="refresh" :size="13" />检查更新
            </GButton>
            <GButton
              v-if="update?.update_available"
              size="sm"
              @click="openRelease(update.release_url)"
            >
              <GIcon name="external" :size="13" />前往 GitHub
            </GButton>
            <GButton
              v-if="update?.update_available && update.can_auto_update"
              size="sm"
              variant="primary"
              :disabled="updateFlow !== 'idle'"
              @click="startDownload"
            >
              <GIcon name="download" :size="13" />立即更新
            </GButton>
          </div>
        </div>

        <!-- 发现新版本：更新公告（点击弹窗查看，支持 Markdown；弹窗在 App.vue 全局渲染） -->
        <template v-if="update?.update_available">
          <SettingRow label="更新公告" :hint="`${update.current_version} → ${update.latest_version}`">
            <GButton size="sm" :disabled="!update.body" @click="openNotice">
              <GIcon name="eye" :size="13" />查看更新公告
            </GButton>
          </SettingRow>
        </template>
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

/* ── 检查间隔：数值输入 + 单位滚动选择器 ── */
.interval-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.interval-row .ctrl-num { width: 92px; }
/* 单位选择器默认 min-width 120px 偏宽，缩窄避免下拉展开超出卡片 */
.interval-row .g-select { width: 92px; min-width: 92px; }
/* 单位下拉面板：选项更紧凑、面板更矮，避免高度溢出覆盖下方内容过多 */
.interval-row :deep(.g-select-pop) {
  max-height: 132px;
  padding: 4px;
}
.interval-row :deep(.g-select-opt) {
  padding: 5px 8px;
  font-size: 11.5px;
}
/* 检查间隔输入框数字与单位文字居中 */
.interval-row :deep(.g-input input) {
  text-align: center;
}
.interval-row :deep(.g-select-label) {
  flex: 1;
  text-align: center;
}
/* 关闭自动检查更新时整行变灰（含 label/说明文字，穿透子组件） */
:deep(.setting-row.interval-disabled) {
  opacity: .55;
  filter: grayscale(.4);
}

/* ── 子设置：相对父设置项缩进，形成层级（如「定时自动备份」展开项）── */
.sub-row {
  padding-left: 24px;
}

/* ── 当前版本 + 检查更新（融合行：左版本信息，右按钮）── */
.update-version-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 0;
  border-top: 1px solid var(--glass-border);
}
.update-version-row:first-child { border-top: none; }
.update-version-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}
.uv-label { font-size: 12.5px; font-weight: 550; }
.update-version { font-size: 12.5px; color: var(--text-1); }
.uv-new { font-size: 11px; color: var(--info); }
.version-badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 7px;
  font-size: 10.5px;
  line-height: 1.5;
  border-radius: 99px;
  color: var(--success);
  background: var(--success-soft);
  border: 1px solid transparent;
}
.version-badge.is-beta {
  color: var(--warn);
  background: var(--warn-soft);
}
.update-version-actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
}

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
