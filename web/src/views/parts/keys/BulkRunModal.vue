<script setup lang="ts">
/* ═══════════════════════════════════════════════════════════════
   BulkRunModal —— 全量「健康检测 / 用量同步」进度模态
   运行开始即打开：动作 + 完成数/总数 + 流光进度条 +
   成功/异常/跳过计数 + 逐 Key 近期结果（最新在顶部）。
   运行中禁关闭（无 ×、Esc/遮罩无效、按钮禁用）；
   完成后可关闭，父级在关闭时刷新 Key 数据。
   ═══════════════════════════════════════════════════════════════ */
import { computed } from 'vue'
import GModal from '@/components/GModal.vue'
import GButton from '@/components/GButton.vue'
import GIcon from '@/components/GIcon.vue'
import type { BulkItemResult } from './utils'

const props = defineProps<{
  open: boolean
  kind: 'health' | 'sync'
  running: boolean
  total: number
  done: number
  /** 逐 Key 结果（新完成项在前） */
  results: BulkItemResult[]
}>()

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
}>()

const META = {
  health: { title: '健康检测', verb: '检测', icon: 'heartPulse' },
  sync: { title: '用量同步', verb: '同步', icon: 'sync' },
} as const

const meta = computed(() => META[props.kind])
const pct = computed(() => (props.total > 0 ? (props.done / props.total) * 100 : 0))

const counts = computed(() => {
  let ok = 0
  let bad = 0
  let skip = 0
  for (const r of props.results) {
    if (r.status === 'ok') ok++
    else if (r.status === 'skip') skip++
    else bad++ // bad + fail 同归「异常」计数
  }
  return { ok, bad, skip }
})

const STATUS_ICON: Record<BulkItemResult['status'], string> = {
  ok: 'check',
  bad: 'alert',
  skip: 'info',
  fail: 'x',
}

function close(): void {
  if (props.running) return
  emit('update:open', false)
}
</script>

<template>
  <GModal
    :open="open"
    :title="meta.title"
    width="520px"
    :closable="!running"
    @update:open="emit('update:open', $event)"
  >
    <!-- 动作状态 + 结果计数 -->
    <div class="bm-head">
      <span class="bm-action" :class="{ live: running }">
        <GIcon :name="meta.icon" :size="14" />
        <template v-if="running">正在{{ meta.verb }}</template>
        <template v-else>{{ meta.verb }}完成</template>
        <b class="u-num">{{ done }}/{{ total }}</b>
      </span>
      <span class="u-grow"></span>
      <span class="bm-stat s-ok"><b class="u-num">{{ counts.ok }}</b>成功</span>
      <span class="bm-stat s-bad"><b class="u-num">{{ counts.bad }}</b>异常</span>
      <span class="bm-stat s-skip"><b class="u-num">{{ counts.skip }}</b>跳过</span>
    </div>

    <!-- 进度条（运行中斜纹流光，完成后转成功色） -->
    <div
      class="bm-track"
      role="progressbar"
      :aria-valuenow="done"
      aria-valuemin="0"
      :aria-valuemax="total"
      :aria-label="`${meta.title}进度`"
    >
      <div class="bm-fill" :class="{ live: running, settled: !running }" :style="{ width: `${pct}%` }" />
    </div>

    <!-- 逐 Key 近期结果（高度受限可滚动，新结果从顶部进入） -->
    <div class="bm-list-wrap">
      <p v-if="!results.length" class="bm-empty">正在建立连接，等待首批结果…</p>
      <TransitionGroup v-else name="bm" tag="ul" class="bm-list">
        <li v-for="r in results" :key="r.masked" class="bm-row">
          <GIcon :name="STATUS_ICON[r.status]" :size="12" class="bm-ic" :class="`s-${r.status}`" />
          <span class="u-mono bm-masked u-ellipsis">{{ r.masked }}</span>
          <span class="bm-detail u-ellipsis" :class="`s-${r.status}`">{{ r.detail }}</span>
        </li>
      </TransitionGroup>
    </div>

    <template #footer>
      <template v-if="running">
        <span class="bm-foot-tip">运行期间不可关闭</span>
        <GButton size="sm" disabled>正在运行…</GButton>
      </template>
      <GButton v-else size="sm" variant="primary" @click="close">完成</GButton>
    </template>
  </GModal>
</template>

<style scoped>
/* ── 状态行 + 计数 ───────────────────────────────────────── */
.bm-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.bm-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-2);
  white-space: nowrap;
}
.bm-action .g-icon { color: var(--accent-text); }
.bm-action.live .g-icon { animation: bm-breathe 1.4s ease-in-out infinite; }
.bm-action b { color: var(--text); font-weight: 650; }

@keyframes bm-breathe {
  0%, 100% { opacity: 1; }
  50% { opacity: .4; }
}

.bm-stat {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-size: 11px;
  color: var(--text-3);
  white-space: nowrap;
}
.bm-stat b { font-size: 13px; font-weight: 650; }
.bm-stat.s-ok b { color: var(--success); }
.bm-stat.s-bad b { color: var(--danger); }
.bm-stat.s-skip b { color: var(--text-2); }

/* ── 进度条 ─────────────────────────────────────────────── */
.bm-track {
  height: 6px;
  border-radius: var(--r-pill);
  background: var(--neutral-soft);
  border: 1px solid var(--glass-border);
  overflow: hidden;
  margin-bottom: 12px;
}
.bm-fill {
  height: 100%;
  border-radius: var(--r-pill);
  background: var(--accent-grad);
  transition: width var(--dur-2) var(--ease-out), background var(--dur-2) ease;
}
.bm-fill.live {
  background-image:
    linear-gradient(45deg, rgba(255, 255, 255, .22) 25%, transparent 25%, transparent 50%,
      rgba(255, 255, 255, .22) 50%, rgba(255, 255, 255, .22) 75%, transparent 75%),
    var(--accent-grad);
  background-size: 16px 16px, 100% 100%;
  animation: bm-stripes .8s linear infinite;
}
@keyframes bm-stripes {
  from { background-position: 0 0, 0 0; }
  to { background-position: 16px 0, 0 0; }
}
.bm-fill.settled {
  background: linear-gradient(90deg, var(--success), color-mix(in srgb, var(--success) 70%, var(--info)));
}

/* ── 逐 Key 结果列表 ────────────────────────────────────── */
.bm-list-wrap { max-height: 216px; overflow-y: auto; }
.bm-list { display: flex; flex-direction: column; }
.bm-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 2px;
  font-size: 11.5px;
  border-bottom: 1px solid var(--glass-border);
}
.bm-row:last-child { border-bottom: none; }
.bm-ic { flex: none; }
.bm-ic.s-ok { color: var(--success); }
.bm-ic.s-bad, .bm-ic.s-fail { color: var(--danger); }
.bm-ic.s-skip { color: var(--text-3); }
.bm-masked { flex: none; max-width: 46%; font-size: 11px; }
.bm-detail { min-width: 0; color: var(--text-3); }
.bm-detail.s-ok { color: var(--text-2); }
.bm-detail.s-bad, .bm-detail.s-fail { color: var(--danger); }

.bm-empty {
  padding: 20px 0;
  text-align: center;
  font-size: 11.5px;
  color: var(--text-3);
}

/* 新结果入场 */
.bm-enter-active {
  transition: opacity var(--dur-2) var(--ease-out), transform var(--dur-2) var(--ease-out);
}
.bm-enter-from { opacity: 0; transform: translateY(-6px); }
.bm-move { transition: transform var(--dur-2) var(--ease-out); }

/* ── 底栏 ───────────────────────────────────────────────── */
.bm-foot-tip { flex: 1 1 auto; font-size: 11px; color: var(--text-3); }
</style>
