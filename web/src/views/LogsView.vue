<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import GlassCard from '@/components/GlassCard.vue'
import GButton from '@/components/GButton.vue'
import GBadge from '@/components/GBadge.vue'
import GIcon from '@/components/GIcon.vue'
import GInput from '@/components/GInput.vue'
import GSelect from '@/components/GSelect.vue'
import GModal from '@/components/GModal.vue'
import Skeleton from '@/components/Skeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import { usePolling } from '@/composables/usePolling'
import { useToast } from '@/composables/useToast'
import {
  exportLogsCsv,
  getLogs,
  saveBlob,
  type LogsPage,
  type LogsQuery,
  type RequestLog,
} from '@/api/client'
import { fmtCredits, fmtLatency, fmtNum, fmtTsFull } from '@/utils/format'

const PAGE_SIZE = 50

const toast = useToast()

/* ── 筛选状态 ─────────────────────────────────────────────── */
const status = ref<'' | 'success' | 'failed'>('')
const endpoint = ref('')
const source = ref('')
const days = ref(7)
const keyword = ref('')          // 输入框即时值
const keywordApplied = ref('')   // 防抖/回车后实际生效值
const offset = ref(0)

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
]
const sourceOptions = [
  { label: '全部来源', value: '' },
  { label: 'MCP', value: 'mcp' },
  { label: '搜索代理', value: 'proxy' },
  { label: 'CLI', value: 'cli' },
]
const daysOptions = [
  { label: '近 1 天', value: 1 },
  { label: '近 3 天', value: 3 },
  { label: '近 7 天', value: 7 },
  { label: '近 30 天', value: 30 },
]

/** 常见接口值 + 从已加载日志中动态收集 */
const KNOWN_ENDPOINTS = ['search', 'extract', 'scrape', 'crawl', 'map', 'research']
const endpointSet = ref<Set<string>>(new Set(KNOWN_ENDPOINTS))
const endpointOptions = computed(() => [
  { label: '全部接口', value: '' },
  ...[...endpointSet.value].map((e) => ({ label: e, value: e })),
])

function collectEndpoints(logs: RequestLog[]): void {
  let changed = false
  const next = new Set(endpointSet.value)
  for (const l of logs) {
    const ep = (l.endpoint || '').trim()
    if (ep && !next.has(ep)) {
      next.add(ep)
      changed = true
    }
  }
  if (changed) endpointSet.value = next
}

/* ── 新日志行高亮 ─────────────────────────────────────────── */
const knownIds = new Set<number>()
const freshIds = ref<Set<number>>(new Set())

function markFresh(logs: RequestLog[]): void {
  // 首批数据（首屏或筛选/翻页后的第一页）不高亮
  if (knownIds.size === 0) {
    for (const l of logs) knownIds.add(l.id)
    return
  }
  const fresh = logs.filter((l) => !knownIds.has(l.id)).map((l) => l.id)
  for (const l of logs) knownIds.add(l.id)
  if (!fresh.length) return
  freshIds.value = new Set([...freshIds.value, ...fresh])
  setTimeout(() => {
    const s = new Set(freshIds.value)
    for (const id of fresh) s.delete(id)
    freshIds.value = s
  }, 2000)
}

/* ── 数据加载（10s 静默轮询，仅第 1 页自动刷新） ──────────── */
let forceFetch = false // 手动刷新 / 筛选 / 翻页时强制拉取

async function fetchLogs(): Promise<LogsPage> {
  if (!forceFetch && offset.value !== 0 && data.value) return data.value
  forceFetch = false
  const page = await getLogs(currentQuery())
  collectEndpoints(page.logs)
  markFresh(page.logs)
  return page
}

const { data, loading, refreshing, error, refresh } = usePolling(fetchLogs, { interval: 10000 })

function currentQuery(): LogsQuery {
  return {
    endpoint: endpoint.value || undefined,
    key: keywordApplied.value || undefined,
    status: status.value,
    source: source.value || undefined,
    days: days.value,
    limit: PAGE_SIZE,
    offset: offset.value,
  }
}

const logs = computed<RequestLog[]>(() => data.value?.logs ?? [])
const total = computed(() => data.value?.total ?? 0)
const pageNo = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const canPrev = computed(() => offset.value > 0)
const canNext = computed(() => offset.value + PAGE_SIZE < total.value)

function applyFilters(): void {
  offset.value = 0
  knownIds.clear()
  freshIds.value = new Set()
  forceFetch = true
  void refresh()
}

function manualRefresh(): void {
  forceFetch = true
  // 手动刷新：跳过 inFlight 防重入，busy 至少 300ms 给按钮可感知的反馈
  void refresh({ force: true, minBusyMs: 300 })
}

function goPage(delta: number): void {
  const next = offset.value + delta * PAGE_SIZE
  if (next < 0 || (delta > 0 && next >= total.value)) return
  offset.value = next
  knownIds.clear()
  freshIds.value = new Set()
  forceFetch = true
  void refresh()
}

/* 关键字：350ms 防抖或回车触发 */
let kwTimer: ReturnType<typeof setTimeout> | null = null
watch(keyword, (v) => {
  if (kwTimer !== null) clearTimeout(kwTimer)
  kwTimer = setTimeout(() => {
    kwTimer = null
    if (v.trim() !== keywordApplied.value) {
      keywordApplied.value = v.trim()
      applyFilters()
    }
  }, 350)
})
function applyKeyword(): void {
  if (kwTimer !== null) {
    clearTimeout(kwTimer)
    kwTimer = null
  }
  const v = keyword.value.trim()
  if (v !== keywordApplied.value) {
    keywordApplied.value = v
    applyFilters()
  }
}

/* ── 导出 CSV ─────────────────────────────────────────────── */
const exporting = ref(false)
async function doExport(): Promise<void> {
  if (exporting.value) return
  exporting.value = true
  try {
    const { blob, filename } = await exportLogsCsv({
      endpoint: endpoint.value || undefined,
      key: keywordApplied.value || undefined,
      status: status.value,
      source: source.value || undefined,
      days: days.value,
    })
    saveBlob(blob, filename)
    toast.success(`日志已导出：${filename}`)
  } catch (e) {
    toast.error('导出失败：' + (e instanceof Error ? e.message : String(e)))
  } finally {
    exporting.value = false
  }
}

/* ── 详情弹窗 ─────────────────────────────────────────────── */
const detailLog = ref<RequestLog | null>(null)
const detailOpen = ref(false)

const SRC_LABEL: Record<string, string> = { mcp: 'MCP', proxy: '搜索代理', cli: 'CLI' }
const USAGE_SRC_LABEL: Record<string, string> = {
  response: '响应',
  unknown: '未知（接口不返回 usage）',
  none: '无',
}

function srcLabel(s: string): string {
  return SRC_LABEL[s] || s || '—'
}

function openDetail(log: RequestLog): void {
  detailLog.value = log
  detailOpen.value = true
}

async function copyRequestId(): Promise<void> {
  const id = detailLog.value?.request_id
  if (!id) return
  await navigator.clipboard.writeText(id)
  toast.success('已复制请求 ID')
}
</script>

<template>
  <div class="view">
    <PageHeader title="请求日志" desc="按状态 / 接口 / 来源 / 时间筛选，支持分页与 CSV 导出">
      <template #actions>
        <GButton size="sm" :busy="refreshing" @click="manualRefresh">
          <GIcon name="refresh" :size="14" /> 刷新
        </GButton>
      </template>
    </PageHeader>

    <!-- 筛选条 -->
    <GlassCard pad="sm">
      <div class="filter-bar">
        <GSelect v-model="status" :options="statusOptions" size="sm" @change="applyFilters" />
        <GSelect v-model="endpoint" :options="endpointOptions" size="sm" @change="applyFilters" />
        <GSelect v-model="source" :options="sourceOptions" size="sm" @change="applyFilters" />
        <GSelect v-model="days" :options="daysOptions" size="sm" @change="applyFilters" />
        <GInput
          v-model="keyword"
          icon="search"
          size="sm"
          clearable
          placeholder="Key 掩码关键字"
          class="kw-input"
          @enter="applyKeyword"
        />
        <GButton size="sm" :busy="exporting" @click="doExport">
          <GIcon name="download" :size="14" /> 导出 CSV
        </GButton>
        <span class="u-muted log-count">
          <template v-if="total">共 {{ fmtNum(total) }} 条</template>
        </span>
      </div>
    </GlassCard>

    <!-- 日志表格 -->
    <GlassCard pad="none">
      <div v-if="loading" class="pad-body">
        <Skeleton :lines="8" />
      </div>

      <EmptyState
        v-else-if="error && !logs.length"
        icon="alert"
        title="加载失败"
        :desc="error.message"
      >
        <GButton size="sm" @click="manualRefresh">重试</GButton>
      </EmptyState>

      <EmptyState
        v-else-if="!logs.length"
        title="暂无匹配的请求日志"
        desc="调整筛选条件或时间范围后再试"
      />

      <template v-else>
        <div class="table-wrap">
          <table class="log-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>Key</th>
                <th>接口</th>
                <th>结果</th>
                <th class="num">积分</th>
                <th class="num">延迟</th>
                <th>来源</th>
                <th>错误信息</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="log in logs"
                :key="log.id"
                :class="{ 'row-fresh': freshIds.has(log.id) }"
                @click="openDetail(log)"
              >
                <td class="u-dim nowrap">{{ fmtTsFull(log.created_at) }}</td>
                <td class="u-mono nowrap">{{ log.key_masked }}</td>
                <td>{{ log.endpoint }}</td>
                <td>
                  <GBadge :type="log.success ? 'success' : 'fail'" dot>
                    {{ log.success ? '成功' : '失败' }}
                  </GBadge>
                </td>
                <td class="num u-num">{{ fmtCredits(log) }}</td>
                <td class="num u-num">{{ fmtLatency(log.latency_ms) }}</td>
                <td class="u-muted nowrap">{{ srcLabel(log.source) }}</td>
                <td class="err-cell" :class="{ 'u-dim': !log.error_msg }">
                  {{ log.error_msg ? log.error_msg.slice(0, 80) : '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 分页 -->
        <div class="pager">
          <GButton size="sm" :disabled="!canPrev" @click="goPage(-1)">
            <GIcon name="chevronLeft" :size="14" /> 上一页
          </GButton>
          <GButton size="sm" :disabled="!canNext" @click="goPage(1)">
            下一页 <GIcon name="chevronRight" :size="14" />
          </GButton>
          <span class="u-muted">第 {{ pageNo }} 页 / 共 {{ pageCount }} 页 · 共 {{ fmtNum(total) }} 条</span>
        </div>
      </template>
    </GlassCard>

    <!-- 日志详情 -->
    <GModal v-model:open="detailOpen" title="请求详情" width="560px">
      <div v-if="detailLog" class="detail">
        <div class="detail-grid">
          <div class="k">时间</div>
          <div class="v">{{ fmtTsFull(detailLog.created_at) }}</div>
          <div class="k">Key</div>
          <div class="v u-mono">{{ detailLog.key_masked }}</div>
          <div class="k">接口</div>
          <div class="v">{{ detailLog.endpoint }}</div>
          <div class="k">结果</div>
          <div class="v">
            <GBadge :type="detailLog.success ? 'success' : 'fail'" dot>
              {{ detailLog.success ? '成功' : '失败' }}
            </GBadge>
            <span v-if="detailLog.is_client_error" class="u-muted">（客户端参数错误）</span>
          </div>
          <div class="k">积分</div>
          <div class="v u-num">{{ fmtCredits(detailLog) }}</div>
          <div class="k">延迟</div>
          <div class="v u-num">{{ fmtLatency(detailLog.latency_ms) }}</div>
          <div class="k">请求 ID</div>
          <div class="v u-flex u-gap-2">
            <span class="u-mono u-ellipsis u-grow">{{ detailLog.request_id || '—' }}</span>
            <GButton v-if="detailLog.request_id" size="sm" text @click="copyRequestId">
              <GIcon name="copy" :size="13" /> 复制
            </GButton>
          </div>
          <div class="k">用量来源</div>
          <div class="v">{{ USAGE_SRC_LABEL[detailLog.usage_source] || detailLog.usage_source || '—' }}</div>
          <div class="k">来源</div>
          <div class="v">{{ srcLabel(detailLog.source) }}</div>
        </div>
        <template v-if="detailLog.error_msg">
          <div class="detail-sec">错误信息</div>
          <pre class="detail-pre">{{ detailLog.error_msg }}</pre>
        </template>
      </div>
      <template #footer>
        <GButton @click="detailOpen = false">关闭</GButton>
      </template>
    </GModal>
  </div>
</template>

<style scoped>
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.kw-input {
  width: 180px;
}
.log-count {
  margin-left: auto;
  font-size: 12px;
  white-space: nowrap;
}

.pad-body {
  padding: 16px;
}

.table-wrap {
  overflow-x: auto;
}
.log-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.log-table th {
  padding: 9px 12px;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--text-3);
  border-bottom: 1px solid var(--glass-border);
  white-space: nowrap;
}
.log-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--glass-border);
  color: var(--text);
  vertical-align: middle;
}
.log-table tbody tr {
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-out);
}
.log-table tbody tr:hover {
  background: var(--accent-softer);
}
.log-table tbody tr:last-child td {
  border-bottom: none;
}
.nowrap {
  white-space: nowrap;
}
.num {
  text-align: right;
  white-space: nowrap;
}
.err-cell {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--danger);
  font-size: 12px;
}
.err-cell.u-dim {
  color: var(--text-3);
}

/* 新日志入场高亮（首屏及筛选后首批不高亮） */
@keyframes row-fresh-in {
  0% {
    background: var(--accent-soft);
    opacity: 0;
    transform: translateY(-6px);
  }
  25% {
    opacity: 1;
    transform: none;
  }
  100% {
    background: transparent;
  }
}
.row-fresh {
  animation: row-fresh-in 1.8s var(--ease-out) both;
}

.pager {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-top: 1px solid var(--glass-border);
  font-size: 12px;
}
.pager .u-muted {
  margin-left: auto;
}

.detail-grid {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 7px 14px;
  font-size: 12.5px;
}
.detail-grid .k {
  color: var(--text-3);
  white-space: nowrap;
}
.detail-grid .v {
  color: var(--text);
  min-width: 0;
  align-items: center;
}
.detail-sec {
  margin-top: 14px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
}
.detail-pre {
  margin: 6px 0 0;
  padding: 10px 12px;
  max-height: 220px;
  overflow: auto;
  border-radius: var(--r-sm);
  border: 1px solid var(--glass-border);
  background: var(--bg-2);
  color: var(--danger);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
