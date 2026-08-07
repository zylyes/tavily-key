<script setup lang="ts">
/* ═══════════════════════════════════════════════════════════════
   KeysView —— API Key 列表（默认首页）
   概览带（聚合指标 + 异常警告）/ 筛选 + Key 表格 / 行内操作 /
   批量操作 / 添加 Key；usePolling(getStats, 5s) 驱动。
   功能对齐旧版 docs/archive/dashboard-legacy.html panel-keys。
   ═══════════════════════════════════════════════════════════════ */
import { computed, reactive, ref, watch, watchEffect } from 'vue'
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
import QuotaBar from '@/components/QuotaBar.vue'
import OverviewBand from './parts/keys/OverviewBand.vue'
import AnomalyDetailModal from './parts/keys/AnomalyDetailModal.vue'
import BulkRunModal from './parts/keys/BulkRunModal.vue'
import { usePolling } from '@/composables/usePolling'
import { useToast } from '@/composables/useToast'
import {
  getStats,
  addKeys,
  removeKey,
  activateKey,
  deactivateKey,
  healthCheckOne,
  syncUsageOne,
} from '@/api/client'
import type { ApiKeyInfo, Anomaly, HealthResult, UsageSyncResult } from '@/api/client'
import { fmtNum, fmtTs, fmtLatency } from '@/utils/format'
import { parseKeysText, runParallel, errMsg, anomalyLabel } from './parts/keys/utils'
import type { BulkItemResult } from './parts/keys/utils'

const toast = useToast()
const { data, loading, error, refresh } = usePolling(getStats, { interval: 5000 })

// ── 派生数据 ─────────────────────────────────────────────────
const keys = computed<ApiKeyInfo[]>(() => data.value?.keys ?? [])
const anomalies = computed<Anomaly[]>(() => data.value?.anomalies ?? [])
const anomalyMap = computed<Record<string, Anomaly>>(() => {
  const m: Record<string, Anomaly> = {}
  for (const a of anomalies.value) m[a.masked] = a
  return m
})

/** 近 24h 聚合：recent_24h 为 { endpoint: { success, failed } } */
const recent24 = computed(() => {
  const r = data.value?.recent_24h
  let success = 0
  let failed = 0
  if (r) {
    for (const ep of Object.keys(r)) {
      success += r[ep]?.success ?? 0
      failed += r[ep]?.failed ?? 0
    }
  }
  return { success, failed, total: success + failed }
})
const requests24h = computed(() => recent24.value.total)
const successRate = computed<number | null>(() =>
  recent24.value.total > 0 ? (recent24.value.success / recent24.value.total) * 100 : null,
)

// ── 筛选（搜索掩码 + 状态）────────────────────────────────────
const query = ref('')
const statusFilter = ref<string | number>('')
const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '活跃', value: 'active' },
  { label: '停用', value: 'inactive' },
  { label: '耗尽', value: 'exhausted' },
  { label: '异常', value: 'anomaly' },
]

const visibleKeys = computed<ApiKeyInfo[]>(() => {
  const q = query.value.trim().toLowerCase()
  const st = String(statusFilter.value)
  return keys.value.filter((k) => {
    if (q && !k.masked.toLowerCase().includes(q)) return false
    if (st === 'active' && !k.is_active) return false
    if (st === 'inactive' && k.is_active) return false
    if (st === 'exhausted' && !k.is_exhausted) return false
    if (st === 'anomaly' && !anomalyMap.value[k.masked]) return false
    return true
  })
})

/** 行内异常徽章（耗尽已由状态列表达，避免重复） */
function anomalyFlagsOf(masked: string): string[] {
  const a = anomalyMap.value[masked]
  if (!a) return []
  return a.flags.filter((f) => f !== 'exhausted')
}

// ── 额度列 ───────────────────────────────────────────────────
const keyLimit = (k: ApiKeyInfo): number => k.credits_limit || k.plan_limit
const hasKeyLimit = (k: ApiKeyInfo): boolean => keyLimit(k) > 0

// ── 选择（跨轮询保持；失效项自动剔除）─────────────────────────
const selected = reactive(new Set<string>())

watch(keys, (list) => {
  const present = new Set(list.map((k) => k.masked))
  for (const m of Array.from(selected)) {
    if (!present.has(m)) selected.delete(m)
  }
})

function toggleSelect(masked: string, ev: Event): void {
  if ((ev.target as HTMLInputElement).checked) selected.add(masked)
  else selected.delete(masked)
}
function clearSelection(): void {
  selected.clear()
}

const allBox = ref<HTMLInputElement | null>(null)
const allChecked = computed(
  () => visibleKeys.value.length > 0 && visibleKeys.value.every((k) => selected.has(k.masked)),
)
const someChecked = computed(() => visibleKeys.value.some((k) => selected.has(k.masked)))
watchEffect(() => {
  if (allBox.value) allBox.value.indeterminate = someChecked.value && !allChecked.value
})
function toggleAll(ev: Event): void {
  const checked = (ev.target as HTMLInputElement).checked
  for (const k of visibleKeys.value) {
    if (checked) selected.add(k.masked)
    else selected.delete(k.masked)
  }
}

// ── 确认弹窗（Promise 化，对齐旧版 confirmDialog）─────────────
const confirmState = reactive({ open: false, title: '', message: '', danger: false })
let confirmResolver: ((v: boolean) => void) | null = null

function askConfirm(title: string, message: string, danger = false): Promise<boolean> {
  confirmState.title = title
  confirmState.message = message
  confirmState.danger = danger
  confirmState.open = true
  return new Promise((res) => {
    confirmResolver = res
  })
}
function settleConfirm(v: boolean): void {
  confirmState.open = false
  const r = confirmResolver
  confirmResolver = null
  r?.(v)
}

// ── 行内操作（busy 按 Key 追踪）───────────────────────────────
type RowAction = 'health' | 'sync' | 'toggle' | 'remove'
const rowBusy = reactive<Record<string, RowAction>>({})

function opDisabled(k: ApiKeyInfo, action: RowAction): boolean {
  const b = rowBusy[k.masked]
  return !!b && b !== action
}

async function onHealthOne(k: ApiKeyInfo): Promise<void> {
  if (rowBusy[k.masked]) return
  rowBusy[k.masked] = 'health'
  try {
    const { result: r } = await healthCheckOne(k.masked)
    if (r.skipped || r.alive === undefined) {
      toast.info(`${k.masked}：已跳过（${r.error || '已停用'}）`)
    } else if (r.alive) {
      toast.success(`${k.masked}：正常${r.latency_ms ? ` · ${fmtLatency(r.latency_ms)}` : ''}`)
    } else {
      toast.error(`${k.masked}：失效（${r.error || '检测失败'}）`)
    }
    await refresh()
  } catch (e) {
    toast.error(`健康检查失败：${errMsg(e)}`)
  } finally {
    delete rowBusy[k.masked]
  }
}

async function onSyncOne(k: ApiKeyInfo): Promise<void> {
  if (rowBusy[k.masked]) return
  rowBusy[k.masked] = 'sync'
  try {
    const { result: r } = await syncUsageOne(k.masked)
    if (r.skipped || r.ok === undefined) {
      toast.info(`${k.masked}：已跳过（${r.error || '已停用'}）`)
    } else if (r.ok) {
      const parts = [`${k.masked}：已同步`]
      if (r.usage != null) parts.push(`用量 ${r.usage}${r.limit != null ? ` / ${r.limit}` : ''}`)
      if (r.recovered) parts.push('额度已恢复')
      toast.success(parts.join('，'))
    } else {
      toast.error(`${k.masked}：同步失败（${r.error || '未知错误'}）`)
    }
    await refresh()
  } catch (e) {
    toast.error(`用量同步失败：${errMsg(e)}`)
  } finally {
    delete rowBusy[k.masked]
  }
}

async function onToggle(k: ApiKeyInfo): Promise<void> {
  if (rowBusy[k.masked]) return
  if (k.is_active) {
    const yes = await askConfirm('停用 Key', `确定停用 ${k.masked} 吗？停用后不再参与请求调度。`)
    if (!yes) return
  }
  rowBusy[k.masked] = 'toggle'
  try {
    if (k.is_active) {
      await deactivateKey(k.masked)
      toast.success(`已停用 ${k.masked}`)
    } else {
      await activateKey(k.masked)
      toast.success(`已启用 ${k.masked}`)
    }
    await refresh()
  } catch (e) {
    toast.error(`操作失败：${errMsg(e)}`)
  } finally {
    delete rowBusy[k.masked]
  }
}

async function onRemove(k: ApiKeyInfo): Promise<void> {
  if (rowBusy[k.masked]) return
  const yes = await askConfirm('删除 Key', `确定删除 ${k.masked} 吗？该操作不可撤销。`, true)
  if (!yes) return
  rowBusy[k.masked] = 'remove'
  try {
    await removeKey(k.masked)
    selected.delete(k.masked)
    toast.success(`已删除 ${k.masked}`)
    await refresh()
  } catch (e) {
    toast.error(`删除失败：${errMsg(e)}`)
  } finally {
    delete rowBusy[k.masked]
  }
}

// ── 批量操作（固定并发 5，失败项保留选择可重试）───────────────
type BatchAction = 'activate' | 'deactivate' | 'remove'
const BATCH_LABEL: Record<BatchAction, string> = { activate: '启用', deactivate: '停用', remove: '删除' }
const BATCH_API: Record<BatchAction, (masked: string) => Promise<unknown>> = {
  activate: (m) => activateKey(m),
  deactivate: (m) => deactivateKey(m),
  remove: (m) => removeKey(m),
}

const batchRunning = ref(false)
const batchDone = ref(0)
const batchTotal = ref(0)

async function runBatch(action: BatchAction): Promise<void> {
  if (batchRunning.value) return
  const targets = Array.from(selected)
  if (!targets.length) return
  const label = BATCH_LABEL[action]
  if (action !== 'activate') {
    const tip = action === 'remove' ? '该操作不可撤销。' : '停用后不再参与请求调度。'
    const yes = await askConfirm(
      `批量${label}`,
      `确定批量${label}选中的 ${targets.length} 个 Key 吗？${tip}`,
      action === 'remove',
    )
    if (!yes) return
  }
  batchRunning.value = true
  batchDone.value = 0
  batchTotal.value = targets.length
  let ok = 0
  let fail = 0
  await runParallel(
    targets,
    async (m) => {
      try {
        await BATCH_API[action](m)
        ok++
        selected.delete(m)
      } catch {
        fail++
      } finally {
        batchDone.value++
      }
    },
    5,
  )
  batchRunning.value = false
  if (fail === 0) toast.success(`批量${label}完成：成功 ${ok}`)
  else if (ok === 0) toast.error(`批量${label}失败：${fail} 个失败`)
  else toast.info(`批量${label}部分完成：成功 ${ok} · 失败 ${fail}（失败项仍保持选中，可重试）`)
  await refresh()
}

// ── 顶部操作 ─────────────────────────────────────────────────
const manualRefresh = ref(false)
async function onRefresh(): Promise<void> {
  if (manualRefresh.value) return
  manualRefresh.value = true
  try {
    // 手动刷新：跳过 inFlight 防重入，busy 至少 300ms 给按钮可感知的反馈
    await refresh({ force: true, minBusyMs: 300 })
  } finally {
    manualRefresh.value = false
  }
}

// ── 全量操作（健康检测 / 用量同步）：逐个真实进度 + 进度模态 ──
// 不用一次性批量 API：runParallel 固定并发 5 逐 Key 执行 healthCheckOne /
// syncUsageOne，每项完成即更新进度模态；保留 正常/异常/跳过/失败 四类结果。
type BulkKind = 'health' | 'sync'

const bulk = reactive({
  open: false,
  kind: 'health' as BulkKind,
  running: false,
  total: 0,
  done: 0,
  results: [] as BulkItemResult[],
})

function mapHealthResult(k: ApiKeyInfo, r: HealthResult): BulkItemResult {
  if (r.skipped || r.alive === undefined) {
    return { masked: k.masked, status: 'skip', detail: `已跳过（${r.error || '已停用'}）` }
  }
  if (r.alive) {
    return { masked: k.masked, status: 'ok', detail: `正常${r.latency_ms ? ` · ${fmtLatency(r.latency_ms)}` : ''}` }
  }
  return { masked: k.masked, status: 'bad', detail: `失效（${r.error || '检测失败'}）` }
}

function mapSyncResult(k: ApiKeyInfo, r: UsageSyncResult): BulkItemResult {
  if (r.skipped || r.ok === undefined) {
    return { masked: k.masked, status: 'skip', detail: `已跳过（${r.error || '已停用'}）` }
  }
  if (r.ok) {
    const parts = ['已同步']
    if (r.usage != null) parts.push(`用量 ${r.usage}${r.limit != null ? ` / ${r.limit}` : ''}`)
    if (r.recovered) parts.push('额度已恢复')
    return { masked: k.masked, status: 'ok', detail: parts.join('，') }
  }
  return { masked: k.masked, status: 'bad', detail: `同步失败（${r.error || '未知错误'}）` }
}

async function runBulk(kind: BulkKind): Promise<void> {
  if (bulk.running) return
  const targets = keys.value // 空集合直接不运行
  if (!targets.length) return
  bulk.kind = kind
  bulk.total = targets.length
  bulk.done = 0
  bulk.results = []
  bulk.running = true
  bulk.open = true // 运行开始即打开进度模态
  let ok = 0
  let bad = 0
  let skip = 0
  await runParallel(
    targets,
    async (k) => {
      let item: BulkItemResult
      try {
        if (kind === 'health') {
          const { result: r } = await healthCheckOne(k.masked)
          item = mapHealthResult(k, r)
        } else {
          const { result: r } = await syncUsageOne(k.masked)
          item = mapSyncResult(k, r)
        }
      } catch (e) {
        item = { masked: k.masked, status: 'fail', detail: `请求失败（${errMsg(e)}）` }
      }
      if (item.status === 'ok') ok++
      else if (item.status === 'skip') skip++
      else bad++ // bad + fail 同归「异常」计数
      bulk.results.unshift(item)
      bulk.done++
    },
    5,
  )
  bulk.running = false
  const label = kind === 'health' ? '健康检测' : '用量同步'
  const msg = `${label}完成：成功 ${ok} · 异常 ${bad}${skip ? ` · 跳过 ${skip}` : ''}`
  if (bad === 0) toast.success(msg)
  else if (ok === 0) toast.error(msg)
  else toast.info(msg)
}

/** 进度模态关闭（运行中 GModal 已禁关闭，这里是完成后）：随后刷新 Key 数据 */
function onBulkModalUpdate(v: boolean): void {
  if (bulk.running) return
  bulk.open = v
  if (!v) refresh()
}

// ── 添加 Key ─────────────────────────────────────────────────
const addOpen = ref(false)
const addText = ref('')
const addBusy = ref(false)
const parsedKeys = computed(() => parseKeysText(addText.value))

function openAdd(): void {
  addText.value = ''
  addOpen.value = true
}

async function submitAdd(): Promise<void> {
  if (!parsedKeys.value.length || addBusy.value) return
  addBusy.value = true
  try {
    const { added } = await addKeys(parsedKeys.value)
    if (added > 0) toast.success(`已添加 ${added} 个 Key`)
    else toast.info('没有新增 Key（可能已存在于池中）')
    addOpen.value = false
    await refresh()
  } catch (e) {
    toast.error(`添加失败：${errMsg(e)}`)
  } finally {
    addBusy.value = false
  }
}

// ── 异常明细 ─────────────────────────────────────────────────
const showAnomalies = ref(false)
</script>

<template>
  <div class="view">
    <PageHeader title="API Key 列表" desc="池内 Key 的启停、健康检查与官方用量同步">
      <template #actions>
        <GButton size="sm" :busy="manualRefresh" @click="onRefresh">
          <GIcon name="refresh" :size="13" />刷新
        </GButton>
        <GButton
          size="sm"
          :busy="bulk.running && bulk.kind === 'health'"
          :disabled="!keys.length || bulk.running"
          @click="runBulk('health')"
        >
          <GIcon name="heartPulse" :size="13" />健康检测
        </GButton>
        <GButton
          size="sm"
          :busy="bulk.running && bulk.kind === 'sync'"
          :disabled="!keys.length || bulk.running"
          @click="runBulk('sync')"
        >
          <GIcon name="sync" :size="13" />用量同步
        </GButton>
        <GButton size="sm" variant="primary" @click="openAdd">
          <GIcon name="plus" :size="13" />添加 Key
        </GButton>
      </template>
    </PageHeader>

    <!-- 首屏骨架 -->
    <template v-if="loading">
      <div class="ov-grid-skel">
        <div v-for="i in 5" :key="i" class="glass ov-skel">
          <Skeleton width="52%" />
          <Skeleton height="20px" width="38%" />
          <Skeleton width="64%" height="11px" />
        </div>
      </div>
      <GlassCard pad="none">
        <div class="skel-body"><Skeleton :lines="5" /></div>
      </GlassCard>
    </template>

    <!-- 加载失败 -->
    <GlassCard v-else-if="!data">
      <EmptyState icon="alert" title="加载失败" :desc="error?.message || '无法获取 Key 池数据'">
        <GButton size="sm" variant="primary" @click="onRefresh">重试</GButton>
      </EmptyState>
    </GlassCard>

    <template v-else>
      <!-- 概览带 + 异常警告条 -->
      <OverviewBand
        :aggregate="data.aggregate"
        :requests24h="requests24h"
        :success-rate="successRate"
        :anomaly-count="anomalies.length"
        @show-anomalies="showAnomalies = true"
      />

      <!-- Key 表格 -->
      <GlassCard pad="none" class="keys-card">
        <div class="toolbar">
          <GInput
            v-model="query"
            size="sm"
            icon="search"
            placeholder="搜索 Key 掩码…"
            clearable
            class="toolbar-search"
          />
          <GSelect v-model="statusFilter" size="sm" :options="statusOptions" class="toolbar-select" />
        </div>

        <EmptyState
          v-if="!keys.length"
          icon="key"
          title="暂无 API Key"
          desc="添加 Tavily API Key 到池中，开始使用 MCP 服务与搜索代理"
        >
          <GButton variant="primary" size="sm" @click="openAdd">
            <GIcon name="plus" :size="13" />添加 Key
          </GButton>
        </EmptyState>
        <EmptyState
          v-else-if="!visibleKeys.length"
          icon="search"
          title="没有匹配的 Key"
          desc="调整搜索关键词或状态筛选"
        />

        <div v-else class="table-wrap">
          <table class="keys-table">
            <thead>
              <tr>
                <th class="col-check">
                  <input
                    ref="allBox"
                    type="checkbox"
                    :checked="allChecked"
                    aria-label="全选当前筛选的 Key"
                    @change="toggleAll"
                  />
                </th>
                <th>Key</th>
                <th>状态</th>
                <th class="col-quota">额度</th>
                <th class="num">请求</th>
                <th class="num">错误</th>
                <th>最后使用</th>
                <th class="col-ops">操作</th>
              </tr>
            </thead>
            <TransitionGroup name="rows" tag="tbody">
              <tr
                v-for="(k, i) in visibleKeys"
                :key="k.masked"
                :style="{ '--i': Math.min(i, 12) }"
                :class="{ selected: selected.has(k.masked) }"
              >
                <td class="col-check">
                  <input
                    type="checkbox"
                    :checked="selected.has(k.masked)"
                    :aria-label="`选择 ${k.masked}`"
                    @change="toggleSelect(k.masked, $event)"
                  />
                </td>
                <td><span class="u-mono key-masked" :title="k.masked">{{ k.masked }}</span></td>
                <td>
                  <div class="u-flex status-cell">
                    <GBadge v-if="k.is_exhausted" type="warn" dot>耗尽</GBadge>
                    <GBadge v-else-if="k.is_active" type="success" dot>活跃</GBadge>
                    <GBadge v-else type="neutral">停用</GBadge>
                    <GBadge v-for="f in anomalyFlagsOf(k.masked)" :key="f" type="warn">
                      {{ anomalyLabel(f) }}
                    </GBadge>
                  </div>
                </td>
                <td
                  class="col-quota"
                  :title="k.usage_synced_at ? `用量同步于 ${fmtTs(k.usage_synced_at)}` : '尚未从官方同步用量'"
                >
                  <QuotaBar
                    v-if="hasKeyLimit(k)"
                    :pct="k.usage_pct"
                    :used="k.credits_used"
                    :limit="keyLimit(k)"
                    height="5px"
                  />
                  <span v-else class="quota-unsynced">未同步</span>
                </td>
                <td class="num u-num">{{ fmtNum(k.request_count) }}</td>
                <td class="num u-num" :class="{ 'err-n': k.error_count > 0 }">{{ fmtNum(k.error_count) }}</td>
                <td><span class="u-dim cell-ts">{{ fmtTs(k.last_used_at) }}</span></td>
                <td>
                  <div class="ops">
                    <GButton
                      text
                      size="sm"
                      :busy="rowBusy[k.masked] === 'health'"
                      :disabled="opDisabled(k, 'health')"
                      title="健康检测"
                      :aria-label="`健康检测 ${k.masked}`"
                      @click="onHealthOne(k)"
                    >
                      <GIcon name="heartPulse" :size="13" />
                    </GButton>
                    <GButton
                      text
                      size="sm"
                      :busy="rowBusy[k.masked] === 'sync'"
                      :disabled="opDisabled(k, 'sync')"
                      title="用量同步"
                      :aria-label="`用量同步 ${k.masked}`"
                      @click="onSyncOne(k)"
                    >
                      <GIcon name="sync" :size="13" />
                    </GButton>
                    <GButton
                      text
                      size="sm"
                      :variant="k.is_active ? 'warn' : 'ghost'"
                      :busy="rowBusy[k.masked] === 'toggle'"
                      :disabled="opDisabled(k, 'toggle')"
                      :title="k.is_active ? '停用' : '启用'"
                      :aria-label="`${k.is_active ? '停用' : '启用'} ${k.masked}`"
                      @click="onToggle(k)"
                    >
                      <GIcon :name="k.is_active ? 'stop' : 'play'" :size="13" />
                    </GButton>
                    <GButton
                      text
                      size="sm"
                      variant="danger"
                      :busy="rowBusy[k.masked] === 'remove'"
                      :disabled="opDisabled(k, 'remove')"
                      title="删除"
                      :aria-label="`删除 ${k.masked}`"
                      @click="onRemove(k)"
                    >
                      <GIcon name="trash" :size="13" />
                    </GButton>
                  </div>
                </td>
              </tr>
            </TransitionGroup>
          </table>
        </div>

        <!-- 批量操作浮动条 -->
        <Transition name="batchbar">
          <div v-if="selected.size" class="batchbar">
            <span class="bb-count u-num">已选 {{ selected.size }} 项</span>
            <span class="bb-sep"></span>
            <span v-if="batchRunning" class="u-muted bb-progress u-num">
              处理中 {{ batchDone }}/{{ batchTotal }}…
            </span>
            <template v-else>
              <GButton size="sm" @click="runBatch('activate')">
                <GIcon name="play" :size="12" />启用
              </GButton>
              <GButton size="sm" variant="warn" @click="runBatch('deactivate')">
                <GIcon name="stop" :size="12" />停用
              </GButton>
              <GButton size="sm" variant="danger" @click="runBatch('remove')">
                <GIcon name="trash" :size="12" />删除
              </GButton>
              <GButton size="sm" text @click="clearSelection">清除</GButton>
            </template>
          </div>
        </Transition>
      </GlassCard>
    </template>

    <!-- 添加 Key -->
    <GModal v-model:open="addOpen" title="添加 API Key" width="520px">
      <p class="modal-tip">每行一个 Key，或用逗号 / 空格分隔；支持批量粘贴。</p>
      <textarea
        v-model="addText"
        class="add-textarea"
        rows="6"
        placeholder="tvly-xxxxxxxxxxxxxxxx&#10;tvly-yyyyyyyyyyyyyyyy"
        spellcheck="false"
      ></textarea>
      <p class="modal-tip add-count">已识别 <b class="u-num">{{ parsedKeys.length }}</b> 个 Key（自动去重）</p>
      <template #footer>
        <GButton @click="addOpen = false">取消</GButton>
        <GButton variant="primary" :busy="addBusy" :disabled="!parsedKeys.length" @click="submitAdd">
          添加{{ parsedKeys.length ? `（${parsedKeys.length}）` : '' }}
        </GButton>
      </template>
    </GModal>

    <!-- 通用确认 -->
    <GModal
      v-model:open="confirmState.open"
      :title="confirmState.title"
      width="420px"
      @close="settleConfirm(false)"
    >
      <p class="confirm-msg">{{ confirmState.message }}</p>
      <template #footer>
        <GButton @click="settleConfirm(false)">取消</GButton>
        <GButton :variant="confirmState.danger ? 'danger' : 'primary'" @click="settleConfirm(true)">
          确定
        </GButton>
      </template>
    </GModal>

    <!-- 异常明细 -->
    <AnomalyDetailModal v-model:open="showAnomalies" :anomalies="anomalies" />

    <!-- 全量健康检测 / 用量同步进度 -->
    <BulkRunModal
      :open="bulk.open"
      :kind="bulk.kind"
      :running="bulk.running"
      :total="bulk.total"
      :done="bulk.done"
      :results="bulk.results"
      @update:open="onBulkModalUpdate"
    />
  </div>
</template>

<style scoped>
/* ── 首屏骨架 ─────────────────────────────────────────────── */
.ov-grid-skel {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}
.ov-skel { padding: 13px 14px 12px; border-radius: var(--r-card); }
.skel-body { padding: 16px 18px; }

/* ── 工具条 ───────────────────────────────────────────────── */
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px 14px;
  border-bottom: 1px solid var(--glass-border);
}
.toolbar-search { flex: 1 1 180px; }
.toolbar-select { flex: 0 0 128px; }

/* ── 表格 ─────────────────────────────────────────────────── */
.keys-card { position: relative; }
.table-wrap { overflow-x: auto; }

.keys-table {
  width: 100%;
  min-width: 880px;
  border-collapse: collapse;
}
.keys-table th {
  padding: 10px 12px;
  font-size: 11px;
  font-weight: 550;
  color: var(--text-3);
  text-align: center;
  white-space: nowrap;
  border-bottom: 1px solid var(--glass-border);
}
.keys-table td {
  padding: 8px 12px;
  font-size: 12px;
  vertical-align: middle;
  text-align: center;
  border-bottom: 1px solid var(--glass-border);
}
.keys-table tbody tr { transition: background var(--dur-1) ease; }
.keys-table tbody tr:last-child td { border-bottom: none; }
.keys-table tbody tr:hover { background: var(--accent-softer); }
.keys-table tbody tr.selected { background: var(--accent-softer); }

.keys-table input[type='checkbox'] {
  accent-color: var(--accent);
  width: 14px;
  height: 14px;
  cursor: pointer;
  vertical-align: middle;
}

.col-check { width: 34px; }
.col-quota { min-width: 150px; }
/* 操作列：固定列宽 = 4 个等宽按钮(26px) + 3 个 gap(2px) + 两侧 padding(12px)，
   标题与按钮组均居中对齐 */
.col-ops { width: 134px; }
.num { white-space: nowrap; }

.key-masked { font-size: 11.5px; letter-spacing: 0.01em; }
.status-cell { gap: 5px; flex-wrap: wrap; }
.quota-unsynced { font-size: 11px; color: var(--warn); }
.cell-ts { font-size: 11px; white-space: nowrap; }
.err-n { color: var(--danger); }
.ops { display: flex; justify-content: center; gap: 2px; }
.ops :deep(.g-btn) { width: 26px; padding: 0; }

/* ── 行增删 / 入场动画（stagger 延迟由 --i 控制）───────────── */
.rows-enter-active {
  transition: opacity var(--dur-3) var(--ease-out), transform var(--dur-3) var(--ease-out);
  transition-delay: calc(var(--i, 0) * 35ms);
}
.rows-enter-from { opacity: 0; transform: translateY(10px); }
.rows-leave-active { transition: opacity var(--dur-2) ease, transform var(--dur-2) ease; }
.rows-leave-to { opacity: 0; transform: translateY(-6px); }
.rows-move { transition: transform var(--dur-3) var(--ease-out); }

/* ── 批量操作浮动条 ───────────────────────────────────────── */
.batchbar {
  position: sticky;
  bottom: 12px;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  margin: 10px auto 12px;
  padding: 7px 12px;
  border-radius: var(--r-pill);
  background: var(--glass-bg-2);
  backdrop-filter: blur(20px) saturate(1.6);
  -webkit-backdrop-filter: blur(20px) saturate(1.6);
  border: 1px solid var(--glass-border-strong);
  box-shadow: var(--shadow-pop);
}
.bb-count { font-size: 12px; font-weight: 600; }
.bb-sep { width: 1px; height: 16px; background: var(--glass-border-strong); }
.bb-progress { font-size: 12px; }

.batchbar-enter-active {
  transition: opacity var(--dur-2) var(--ease-spring), transform var(--dur-2) var(--ease-spring);
}
.batchbar-leave-active { transition: opacity var(--dur-1) ease, transform var(--dur-1) ease; }
.batchbar-enter-from,
.batchbar-leave-to { opacity: 0; transform: translateY(10px) scale(0.96); }

/* ── 弹窗 ─────────────────────────────────────────────────── */
.modal-tip { font-size: 11.5px; color: var(--text-3); margin-bottom: 8px; }
.add-count { margin: 8px 0 0; }
.add-count b { color: var(--accent-text); }

.add-textarea {
  width: 100%;
  min-height: 120px;
  resize: vertical;
  padding: 10px 12px;
  border-radius: var(--r-ctrl);
  background: var(--input-bg);
  border: 1px solid var(--glass-border);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  transition: border-color var(--dur-1) ease, box-shadow var(--dur-1) ease;
}
.add-textarea::placeholder { color: var(--text-3); }
.add-textarea:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.confirm-msg { font-size: 12.5px; color: var(--text-2); line-height: 1.6; }
</style>
