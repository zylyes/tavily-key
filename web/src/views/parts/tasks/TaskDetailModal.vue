<script setup lang="ts">
/* TaskDetailModal —— Research 任务详情模态（对齐旧版 taskDetail） */
import GModal from '@/components/GModal.vue'
import GButton from '@/components/GButton.vue'
import GBadge from '@/components/GBadge.vue'
import GIcon from '@/components/GIcon.vue'
import { useToast } from '@/composables/useToast'
import type { ResearchTask } from '@/api/client'
import { taskContentText, taskStatusLabel, taskStatusType } from './taskMeta'

const props = defineProps<{
  open: boolean
  task: ResearchTask | null
}>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
}>()

const toast = useToast()

async function copyId(): Promise<void> {
  if (!props.task?.request_id) return
  try {
    await navigator.clipboard.writeText(props.task.request_id)
    toast.success('已复制 request_id')
  } catch {
    toast.error('复制失败')
  }
}
</script>

<template>
  <GModal
    :open="open"
    title="Research 任务详情"
    width="560px"
    @update:open="emit('update:open', $event)"
  >
    <template v-if="task">
      <div class="td-grid">
        <div class="td-k">请求 ID</div>
        <div class="td-v u-mono">{{ task.request_id }}</div>
        <div class="td-k">Key</div>
        <div class="td-v u-mono">{{ task.masked }}</div>
        <div class="td-k">状态</div>
        <div class="td-v u-flex u-gap-2">
          <GBadge :type="taskStatusType(task.status)">{{ taskStatusLabel(task.status) }}</GBadge>
          <GBadge v-if="task.cached" type="neutral">缓存</GBadge>
        </div>
      </div>

      <template v-if="task.status === 'completed' && taskContentText(task)">
        <div class="td-sec">内容摘要（预览）</div>
        <pre class="td-pre good">{{ taskContentText(task) }}</pre>
      </template>
      <template v-else-if="task.error">
        <div class="td-sec">错误信息</div>
        <pre class="td-pre">{{ task.error }}</pre>
      </template>
      <p v-else-if="task.status === 'unknown'" class="td-hint">
        缓存未刷新（点「刷新」后查询）
      </p>
    </template>

    <template #footer>
      <GButton size="sm" @click="copyId">
        <GIcon name="copy" :size="13" />复制 request_id
      </GButton>
      <GButton size="sm" variant="primary" @click="emit('update:open', false)">关闭</GButton>
    </template>
  </GModal>
</template>

<style scoped>
.td-grid {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 8px 14px;
  font-size: 12px;
}
.td-k { color: var(--text-3); }
.td-v { min-width: 0; word-break: break-all; }

.td-sec {
  margin-top: 14px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-3);
}
.td-pre {
  margin-top: 6px;
  padding: 10px 12px;
  max-height: 300px;
  overflow: auto;
  border-radius: var(--r-sm);
  border: 1px solid var(--glass-border);
  background: var(--input-bg);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  user-select: text;
}
.td-pre.good { border-color: color-mix(in srgb, var(--success) 30%, transparent); }

.td-hint { margin-top: 12px; font-size: 12px; color: var(--text-2); }
</style>
