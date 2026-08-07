<script setup lang="ts">
/* TasksView —— Research 任务看板（getResearchTasks，10s 轮询；对齐旧版 tasks panel） */
import { computed, ref } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import GlassCard from '@/components/GlassCard.vue'
import GButton from '@/components/GButton.vue'
import GBadge from '@/components/GBadge.vue'
import GIcon from '@/components/GIcon.vue'
import Skeleton from '@/components/Skeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import { usePolling } from '@/composables/usePolling'
import { useToast } from '@/composables/useToast'
import { getResearchTasks, retryResearchTask, type ResearchTask } from '@/api/client'
import TaskDetailModal from './parts/tasks/TaskDetailModal.vue'
import { isTaskDone, taskDetailPreview, taskStatusLabel, taskStatusType } from './parts/tasks/taskMeta'

const toast = useToast()
const { data, loading, refreshing, error, refresh } = usePolling(
  () => getResearchTasks(50),
  { interval: 10000 },
)

// 手动刷新：跳过 inFlight 防重入，busy 至少 300ms 给按钮可感知的反馈
function onManualRefresh(): void {
  void refresh({ force: true, minBusyMs: 300 })
}

const tasks = computed(() => data.value?.tasks ?? [])
const doneCount = computed(() => tasks.value.filter((t) => isTaskDone(t.status)).length)

/** 可一键重试的状态：失败 / 错误（取消是用户主动行为，不重试） */
function isRetryable(t: ResearchTask): boolean {
  return t.status === 'failed' || t.status === 'error'
}

const retrying = ref<string | null>(null)
async function onRetry(t: ResearchTask): Promise<void> {
  if (retrying.value) return
  retrying.value = t.request_id
  try {
    const r = await retryResearchTask(t.request_id)
    toast.success(`已重新提交：${r.request_id}`)
    void refresh({ force: true })
  } catch (e) {
    toast.error('重试失败：' + (e instanceof Error ? e.message : String(e)))
  } finally {
    retrying.value = null
  }
}

const detailOpen = ref(false)
const selected = ref<ResearchTask | null>(null)
function openDetail(t: ResearchTask): void {
  selected.value = t
  detailOpen.value = true
}
</script>

<template>
  <div class="view">
    <PageHeader
      title="Research 任务"
      desc="wait=false 提交的异步研究任务 · 查询带缓存，每 10s 自动刷新"
    >
      <template #actions>
        <GButton size="sm" :busy="refreshing" @click="onManualRefresh">
          <GIcon name="refresh" :size="13" />刷新
        </GButton>
      </template>
    </PageHeader>

    <GlassCard v-if="loading">
      <Skeleton :lines="5" />
    </GlassCard>

    <EmptyState
      v-else-if="!tasks.length"
      :title="error ? '加载失败' : '暂无 Research 任务'"
      :desc="error ? error.message : 'wait=false 提交的异步研究任务会显示在这里'"
    />

    <GlassCard v-else pad="none" class="tasks-card">
      <div class="tasks-summary">
        <span class="tasks-dot" :class="doneCount === tasks.length ? 'ok' : 'warn'" />
        <span class="u-muted">
          共 <b class="u-num">{{ tasks.length }}</b> 个任务 · 已结束
          <b class="u-num">{{ doneCount }}</b>
        </span>
      </div>
      <table class="tasks-table">
        <thead>
          <tr>
            <th style="width: 30%">request_id</th>
            <th style="width: 22%">Key</th>
            <th style="width: 15%">状态</th>
            <th>详情</th>
            <th style="width: 70px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="t in tasks"
            :key="t.request_id"
            class="tasks-row"
            title="点击查看完整详情"
            @click="openDetail(t)"
          >
            <td class="u-mono u-ellipsis tasks-id" :title="t.request_id">{{ t.request_id }}</td>
            <td class="u-mono u-ellipsis">{{ t.masked }}</td>
            <td>
              <GBadge :type="taskStatusType(t.status)">{{ taskStatusLabel(t.status) }}</GBadge>
              <GBadge v-if="t.cached" type="neutral" class="tasks-cached">缓存</GBadge>
            </td>
            <td class="u-ellipsis u-muted">{{ taskDetailPreview(t) }}</td>
            <td class="tasks-ops">
              <GButton
                v-if="isRetryable(t)"
                text
                size="sm"
                :busy="retrying === t.request_id"
                title="用原参数重新提交"
                @click.stop="onRetry(t)"
              >
                <GIcon name="refresh" :size="13" />重试
              </GButton>
            </td>
          </tr>
        </tbody>
      </table>
    </GlassCard>

    <TaskDetailModal v-model:open="detailOpen" :task="selected" />
  </div>
</template>

<style scoped>
.tasks-card { overflow: hidden; }

.tasks-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  font-size: 12px;
  border-bottom: 1px solid var(--glass-border);
}
.tasks-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; }
.tasks-dot.ok { background: var(--success); box-shadow: 0 0 8px var(--success); }
.tasks-dot.warn { background: var(--warn); box-shadow: 0 0 8px var(--warn); }

.tasks-table { width: 100%; table-layout: fixed; border-collapse: collapse; }
.tasks-table th {
  padding: 9px 16px 7px;
  font-size: 11px;
  font-weight: 600;
  text-align: left;
  color: var(--text-3);
  border-bottom: 1px solid var(--glass-border);
}
.tasks-table td {
  padding: 9px 16px;
  font-size: 12px;
  border-bottom: 1px solid var(--glass-border);
}
.tasks-table tbody tr:last-child td { border-bottom: none; }

.tasks-row { cursor: pointer; transition: background var(--dur-1) ease; }
.tasks-row:hover { background: var(--neutral-soft); }

.tasks-id { font-size: 11px; color: var(--text-3); }
.tasks-cached { margin-left: 6px; }

/* 操作列：重试按钮（阻止行点击冒泡已在模板 .stop 处理） */
.tasks-ops { text-align: right; white-space: nowrap; }
.tasks-ops :deep(.g-btn) { color: var(--accent-text); }
</style>
