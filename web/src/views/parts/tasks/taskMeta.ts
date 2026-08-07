/* Research 任务状态元数据 —— 与旧版 docs/archive/dashboard-legacy.html loadTasks / taskDetail 的映射一致 */
import type { ResearchTask } from '@/api/client'

export type TaskBadgeType = 'success' | 'fail' | 'warn' | 'info' | 'neutral'

const STATUS_LABEL: Record<string, string> = {
  completed: '完成',
  submitted: '进行中',
  pending: '进行中',
  running: '进行中',
  in_progress: '进行中',
  processing: '进行中',
  failed: '失败',
  error: '错误',
  cancelled: '已取消',
  unknown: '未知',
}

export function taskStatusLabel(status: string): string {
  return STATUS_LABEL[status] || status || '未知'
}

export function taskStatusType(status: string): TaskBadgeType {
  if (status === 'completed') return 'success'
  if (status === 'failed' || status === 'error') return 'fail'
  if (status === 'unknown') return 'warn'
  if (status === 'cancelled') return 'neutral'
  if (STATUS_LABEL[status]) return 'info' // submitted/pending/running/… → 蓝
  return 'neutral'
}

/** 是否已结束（用于「共 N · 已结束 M」汇总，与旧版一致） */
export function isTaskDone(status: string): boolean {
  return ['completed', 'failed', 'error', 'cancelled'].includes(status)
}

/** content 统一转文本（后端可能返回对象） */
export function taskContentText(t: ResearchTask): string {
  const c = t.content
  if (c === undefined || c === null) return ''
  return typeof c === 'string' ? c : JSON.stringify(c, null, 2)
}

/** 表格「详情」列预览文案（对齐旧版：完成→60字摘要 / 错误→80字 / unknown→提示） */
export function taskDetailPreview(t: ResearchTask): string {
  if (t.status === 'completed') {
    const text = taskContentText(t).replace(/\s+/g, ' ').trim()
    if (!text) return '—'
    return text.length > 60 ? `${text.slice(0, 60)}…` : text
  }
  if (t.error) return String(t.error).slice(0, 80)
  if (t.status === 'unknown') return '缓存未刷新（点「刷新」后查询）'
  return '—'
}
