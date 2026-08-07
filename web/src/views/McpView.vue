<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import GlassCard from '@/components/GlassCard.vue'
import GButton from '@/components/GButton.vue'
import GBadge from '@/components/GBadge.vue'
import GIcon from '@/components/GIcon.vue'
import GInput from '@/components/GInput.vue'
import GSelect from '@/components/GSelect.vue'
import GSwitch from '@/components/GSwitch.vue'
import Skeleton from '@/components/Skeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import { usePolling } from '@/composables/usePolling'
import { useToast } from '@/composables/useToast'
import {
  ApiError,
  generateMcpToken,
  getMcpStatus,
  getSettings,
  saveSettings,
  startMcp,
  stopMcp,
} from '@/api/client'

const toast = useToast()
const { data: status, loading, refreshing, error, refresh } =
  usePolling(getMcpStatus, { interval: 5000 })

// 手动刷新：跳过 inFlight 防重入，busy 至少 300ms 给按钮可感知的反馈
function onManualRefresh(): void {
  void refresh({ force: true, minBusyMs: 300 })
}

// ── 复制 ─────────────────────────────────────────────────────
async function copyText(text: string, msg = '已复制'): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    if (msg) toast.success(msg)
  } catch {
    toast.error('复制失败，请手动选择复制')
  }
}

// ── 启停 ─────────────────────────────────────────────────────
const actionBusy = ref(false)
const actionError = ref('')

async function onToggle(): Promise<void> {
  const s = status.value
  if (!s || !s.network || actionBusy.value) return
  const wasRunning = s.running
  actionBusy.value = true
  actionError.value = ''
  try {
    const d = wasRunning ? await stopMcp() : await startMcp()
    if (!d.ok) {
      actionError.value = d.error || '操作失败'
      toast.error(d.error || '操作失败')
    } else if (wasRunning) {
      toast.success('MCP 服务已停止')
    } else {
      const url = d.status?.url || ''
      if (url) {
        try {
          await navigator.clipboard.writeText(url)
          toast.success('MCP 服务已启动，地址已复制到剪贴板')
        } catch {
          toast.success('MCP 服务已启动')
        }
      } else {
        toast.success('MCP 服务已启动')
      }
    }
  } catch (e) {
    actionError.value = e instanceof ApiError ? e.message : '操作失败，请稍后重试'
    toast.error(actionError.value)
  } finally {
    actionBusy.value = false
    await refresh()
  }
}

// ── 状态展示 ─────────────────────────────────────────────────
const transportLabel = computed(() => {
  const t = status.value?.transport || ''
  if (t === 'streamable-http') return 'Streamable HTTP'
  if (t === 'sse') return 'SSE'
  return t || '—'
})

interface UrlEntry { key: string; label: string; hint: string; value: string }

const urlEntries = computed<UrlEntry[]>(() => {
  const s = status.value
  if (!s || !s.running || !s.network) return []
  const defs = [
    { key: 'ip', label: '局域网 IP 地址', hint: '切换网络后可能变化' },
    { key: 'hostname_local', label: '主机名地址', hint: '切换网络不变（mDNS，客户端需支持 .local 解析）' },
    { key: 'local', label: '本机地址', hint: '仅本机可用，不依赖网络' },
  ]
  return defs.flatMap((d) => {
    const value = s.urls?.[d.key]
    return value ? [{ ...d, value }] : []
  })
})

const tokenNote = computed(() => {
  const s = status.value
  if (!s) return ''
  return s.token_set
    ? '已启用访问密钥，客户端需携带 Authorization: Bearer <密钥> 请求头。'
    : '未设置访问密钥，MCP 服务对外开放，任何能访问该地址的设备都可能消耗 Key 池额度。'
})

// 状态卡只展示脱敏 token；复制时优先取设置表单中的完整值（与旧版行为一致）
function onCopyToken(): void {
  const s = status.value
  const full = token.value || s?.token || ''
  if (!full) return
  void copyText(full, 'MCP 访问令牌已复制')
}

// ── 生成新密钥 ───────────────────────────────────────────────
const generating = ref(false)

async function onGenerateToken(): Promise<void> {
  if (generating.value) return
  generating.value = true
  try {
    const d = await generateMcpToken()
    if (d.ok && d.token) {
      token.value = d.token
      mcpTokenSet.value = true
      await refresh()
      void copyText(d.token, '新密钥已生成并复制到剪贴板')
    } else {
      toast.error('生成失败，请稍后重试')
    }
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : '生成失败，请稍后重试')
  } finally {
    generating.value = false
  }
}

// ── 设置表单 ─────────────────────────────────────────────────
const settingsLoaded = ref(false)
const settingsError = ref('')
const mcpTokenSet = ref(false)

const autoStart = ref(false)
const transport = ref<string | number>('sse')
const host = ref('0.0.0.0')
const port = ref('8001')
const token = ref('')
const projectId = ref('')
const humanId = ref('')
const defaultParams = ref('')

const transportOptions = [
  { label: 'SSE', value: 'sse', hint: '默认 · 局域网可用' },
  { label: 'Streamable HTTP', value: 'streamable-http', hint: '局域网可用' },
  { label: 'stdio', value: 'stdio', hint: '客户端直连 · 面板不可管理' },
]

async function loadSettings(): Promise<void> {
  settingsError.value = ''
  try {
    const resp = await getSettings()
    const s = resp.settings
    autoStart.value = !!s.mcp_auto_start
    transport.value = s.mcp_transport || 'sse'
    host.value = s.mcp_host || '0.0.0.0'
    port.value = String(s.mcp_port || 8001)
    token.value = s.mcp_token || ''
    projectId.value = s.mcp_project_id || ''
    humanId.value = s.mcp_human_id || ''
    const dp = s.mcp_default_parameters
    defaultParams.value = dp && Object.keys(dp).length ? JSON.stringify(dp, null, 2) : ''
    mcpTokenSet.value = !!s.mcp_token
    settingsLoaded.value = true
  } catch (e) {
    settingsError.value = e instanceof ApiError ? e.message : '设置加载失败'
  }
}

onMounted(loadSettings)

function applyPreset(): void {
  const preset = { search_depth: 'advanced', chunks_per_source: 3, max_results: 5 }
  defaultParams.value = JSON.stringify(preset, null, 2)
  toast.info('已填入官方推荐默认参数，可再修改后保存')
}

const saving = ref(false)
const saveError = ref('')

async function onSave(): Promise<void> {
  if (saving.value) return
  const raw = defaultParams.value.trim()
  let dp: Record<string, unknown> = {}
  if (raw) {
    try {
      dp = JSON.parse(raw) as Record<string, unknown>
    } catch {
      saveError.value = '默认参数 JSON 格式错误'
      toast.error('默认参数 JSON 格式错误')
      return
    }
    if (typeof dp !== 'object' || dp === null || Array.isArray(dp)) {
      saveError.value = '默认参数必须是 JSON 对象'
      toast.error('默认参数必须是 JSON 对象')
      return
    }
  }
  saving.value = true
  saveError.value = ''
  try {
    await saveSettings({
      mcp_auto_start: autoStart.value,
      mcp_transport: String(transport.value) as 'stdio' | 'sse' | 'streamable-http',
      mcp_host: host.value.trim(),
      mcp_port: parseInt(port.value, 10) || 8001,
      mcp_token: token.value.trim(),
      mcp_project_id: projectId.value.trim(),
      mcp_human_id: humanId.value.trim(),
      mcp_default_parameters: dp,
    })
    mcpTokenSet.value = !!token.value.trim()
    toast.success('MCP 设置已保存')
    await refresh()
  } catch (e) {
    saveError.value = e instanceof ApiError ? e.message : '保存失败，请稍后重试'
    toast.error(saveError.value)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="view">
    <PageHeader title="MCP 服务" desc="面向 AI 客户端 MCP 服务：状态、启停与密钥管理">
      <template #actions>
        <GButton size="sm" :busy="refreshing" @click="onManualRefresh">
          <GIcon name="refresh" :size="13" />刷新
        </GButton>
      </template>
    </PageHeader>

    <GlassCard v-if="loading">
      <Skeleton :lines="5" />
    </GlassCard>

    <GlassCard v-else-if="!status">
      <EmptyState icon="alert" title="状态加载失败" :desc="error?.message || '无法获取 MCP 服务状态'">
        <GButton size="sm" variant="primary" @click="onManualRefresh">重试</GButton>
      </EmptyState>
    </GlassCard>

    <div v-else class="stagger card-stack">
      <!-- ── 状态卡 ── -->
      <GlassCard title="MCP 服务状态" desc="把 Key 池包装为 MCP 服务，客户端填入「服务地址」即可直接调用">
        <template #actions>
          <GButton v-if="!status.network" disabled title="stdio 模式由 AI 客户端拉起，面板不可启停">
            stdio 不可启停
          </GButton>
          <GButton
            v-else
            :variant="status.running ? 'danger' : 'primary'"
            :busy="actionBusy"
            @click="onToggle"
          >
            <GIcon :name="status.running ? 'stop' : 'play'" :size="13" />
            {{ status.running ? '停止服务' : '启动服务' }}
          </GButton>
        </template>

        <div class="status-row">
          <GBadge v-if="!status.network" type="info">stdio</GBadge>
          <GBadge v-else-if="status.running" type="success">
            <i class="run-dot pulse-dot" aria-hidden="true" />运行中
          </GBadge>
          <GBadge v-else type="neutral">已停止</GBadge>
          <span v-if="status.running && status.pid" class="pid u-num">PID {{ status.pid }}</span>
          <span v-if="status.network" class="meta-chip" title="传输方式">{{ transportLabel }}</span>
          <span v-if="status.network" class="meta-chip u-mono" title="监听地址">{{ status.host }}:{{ status.port }}</span>
          <span v-if="status.auto_start" class="meta-chip" title="随软件启动">自启动</span>
        </div>

        <template v-if="status.running && status.network">
          <div v-if="status.url" class="url-hero">
            <span class="url-hero-value u-mono u-ellipsis">{{ status.url }}</span>
            <GButton size="sm" variant="primary" @click="copyText(status.url, '服务地址已复制')">
              <GIcon name="copy" :size="13" />复制
            </GButton>
          </div>

          <div class="url-label">服务地址（填入客户端的「服务地址」字段）</div>
          <div v-for="entry in urlEntries" :key="entry.key" class="url-item">
            <div class="url-item-row">
              <span class="url-item-label">{{ entry.label }}</span>
              <span class="url-item-value u-mono u-ellipsis">{{ entry.value }}</span>
              <GButton size="sm" @click="copyText(entry.value, '地址已复制')">
                <GIcon name="copy" :size="13" />复制
              </GButton>
            </div>
            <div class="url-item-hint">{{ entry.hint }}</div>
          </div>
        </template>

        <!-- 密钥区与运行状态解耦：停止时也允许查看/生成令牌（先配密钥再启动是合法流程） -->
        <template v-if="status">
          <div class="url-label">API 密钥（填入客户端的「API 密钥」字段）</div>
          <div class="token-row">
            <span class="url-item-value u-mono u-ellipsis u-grow">{{ status.token || '—' }}</span>
            <GBadge :type="status.token_set ? 'success' : 'warn'">
              {{ status.token_set ? '已设置密钥' : '未设置密钥' }}
            </GBadge>
            <GButton size="sm" :disabled="!status.token_set && !token" @click="onCopyToken">
              <GIcon name="copy" :size="13" />复制
            </GButton>
            <GButton size="sm" variant="primary" :busy="generating" @click="onGenerateToken">
              <GIcon name="key" :size="13" />生成新密钥
            </GButton>
          </div>
          <p class="field-hint" :class="{ 'warn-text': !status.token_set }">
            <GIcon v-if="!status.token_set" name="alert" :size="12" class="warn-icon" />{{ tokenNote }}
          </p>
        </template>

        <p v-if="actionError" class="action-error">{{ actionError }}</p>
      </GlassCard>

      <!-- ── 设置卡 ── -->
      <GlassCard title="MCP 设置" desc="配置启动方式、监听地址与访问密钥">
        <Skeleton v-if="!settingsLoaded && !settingsError" :lines="6" />
        <div v-else-if="settingsError" class="settings-error">
          <span>设置加载失败：{{ settingsError }}</span>
          <GButton size="sm" @click="loadSettings">重试</GButton>
        </div>
        <form v-else @submit.prevent="onSave">
          <div class="form-row">
            <label>随软件启动</label>
            <GSwitch v-model="autoStart" label="软件启动后自动启动 MCP 服务" />
          </div>

          <div class="form-row">
            <label>传输方式</label>
            <GSelect v-model="transport" :options="transportOptions" />
            <p v-if="transport === 'stdio'" class="field-hint">
              stdio 由 AI 客户端本机直连拉起，无法由面板启停；监听地址与端口仅对网络模式生效。
            </p>
          </div>

          <div class="form-grid">
            <div class="form-row">
              <label>监听地址</label>
              <GInput v-model="host" mono placeholder="0.0.0.0（局域网）/ 127.0.0.1（仅本机）" />
            </div>
            <div class="form-row">
              <label>端口</label>
              <GInput v-model="port" type="number" mono placeholder="8001" />
            </div>
          </div>

          <div class="form-row">
            <label>访问密钥（留空则不鉴权）</label>
            <GInput
              v-model="token"
              type="password"
              mono
              placeholder="非空时客户端需带 Authorization: Bearer <token> 请求头"
              autocomplete="off"
            />
          </div>

          <div class="form-grid">
            <div class="form-row">
              <label>项目归属 ID（可选）</label>
              <GInput v-model="projectId" mono placeholder="转发 X-Project-ID 头，按项目归类用量" />
            </div>
            <div class="form-row">
              <label>会话归属 ID（可选）</label>
              <GInput v-model="humanId" mono placeholder="转发 X-Human-Id 头，便于会话分析" />
            </div>
          </div>

          <div class="form-row">
            <label>默认参数（JSON，可选）</label>
            <div class="params-row">
              <textarea
                v-model="defaultParams"
                class="params-input"
                rows="4"
                placeholder='{"search_depth":"advanced","chunks_per_source":3,"max_results":5}'
                spellcheck="false"
              />
              <GButton size="sm" class="params-preset" @click="applyPreset">推荐预设</GButton>
            </div>
            <p class="field-hint">
              对所有 search 类请求注入的默认参数（客户端显式传入的参数优先）。提示：<code>auto_parameters</code>
              可能自动把 <code>search_depth</code> 升为 <code>advanced</code>（每次 2 积分），
              需要控制成本时请显式指定 <code>search_depth</code>。
            </p>
          </div>

          <div class="form-actions">
            <GButton variant="primary" type="submit" :busy="saving">
              <GIcon name="check" :size="13" />保存设置
            </GButton>
            <span v-if="saveError" class="save-error">{{ saveError }}</span>
          </div>
        </form>
      </GlassCard>
    </div>

  </div>
</template>

<style scoped>
/* 双卡片容器：仅承担纵向布局，入场动画仍由全局 .stagger 负责 */
.card-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.status-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.run-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}
.pid { font-size: 11px; color: var(--text-3); }
.meta-chip {
  font-size: 11px;
  padding: 3px 9px;
  border-radius: var(--r-pill);
  background: var(--neutral-soft);
  border: 1px solid var(--glass-border);
  color: var(--text-2);
  white-space: nowrap;
}

.url-hero {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
  padding: 12px 14px;
  border-radius: var(--r-ctrl);
  background: var(--accent-softer);
  border: 1px solid color-mix(in srgb, var(--accent) 22%, transparent);
}
.url-hero-value {
  flex: 1;
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--accent-text);
}

.url-label {
  margin: 16px 0 8px;
  font-size: 12px;
  font-weight: 550;
  color: var(--text-2);
}
.url-item { margin-bottom: 8px; }
.url-item-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.url-item-label {
  flex: none;
  width: 92px;
  font-size: 11.5px;
  color: var(--text-2);
}
.url-item-value {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  font-size: 12px;
  border-radius: var(--r-sm);
  background: var(--input-bg);
  border: 1px solid var(--glass-border);
  color: var(--text);
}
.url-item-hint {
  margin-top: 3px;
  padding-left: 100px;
  font-size: 11px;
  color: var(--text-3);
}

.token-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.field-hint.warn-text { color: var(--warn); }
.warn-icon { vertical-align: -2px; margin-right: 4px; }
.action-error {
  margin-top: 10px;
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--danger);
  background: var(--danger-soft);
  border: 1px solid color-mix(in srgb, var(--danger) 28%, transparent);
  border-radius: var(--r-sm);
}

/* ── 设置表单 ── */
.form-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 14px;
}
.form-row > label {
  font-size: 12px;
  font-weight: 550;
  color: var(--text-2);
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 14px;
}
@media (max-width: 720px) {
  .form-grid { grid-template-columns: 1fr; }
  .card-stack { gap: 12px; }
}
.field-hint {
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-3);
}
.field-hint code {
  font-family: var(--font-mono);
  font-size: 10.5px;
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--neutral-soft);
}
.params-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}
.params-input {
  flex: 1;
  min-width: 0;
  min-height: 88px;
  padding: 9px 12px;
  resize: vertical;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
  background: var(--input-bg);
  border: 1px solid var(--glass-border);
  border-radius: var(--r-ctrl);
  transition: border-color var(--dur-1) ease, box-shadow var(--dur-1) ease;
}
.params-input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.params-preset { flex: none; }
.form-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
}
.save-error { font-size: 12px; color: var(--danger); }
.settings-error {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--danger);
}

</style>
