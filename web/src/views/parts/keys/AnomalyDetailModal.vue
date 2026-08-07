<script setup lang="ts">
/* AnomalyDetailModal —— 异常 Key 明细（masked / flags / reasons / 用量） */
import GModal from '@/components/GModal.vue'
import GBadge from '@/components/GBadge.vue'
import GButton from '@/components/GButton.vue'
import type { Anomaly } from '@/api/client'
import { fmtNum, fmtPct } from '@/utils/format'
import { anomalyLabel, SEVERE_FLAGS } from './utils'

defineProps<{
  open: boolean
  anomalies: Anomaly[]
}>()

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
}>()

function close(): void {
  emit('update:open', false)
}
</script>

<template>
  <GModal
    :open="open"
    :title="`异常明细（${anomalies.length}）`"
    width="560px"
    @update:open="emit('update:open', $event)"
  >
    <div class="an-list">
      <div v-for="a in anomalies" :key="a.masked" class="an-row">
        <div class="an-head">
          <span class="u-mono an-masked u-ellipsis">{{ a.masked }}</span>
          <GBadge v-if="a.is_exhausted" type="warn" dot>耗尽</GBadge>
          <GBadge v-else-if="a.is_active" type="success" dot>活跃</GBadge>
          <GBadge v-else type="neutral">停用</GBadge>
          <span class="u-grow"></span>
          <span class="an-usage u-num">
            {{ fmtNum(a.credits_used) }} / {{ a.credits_limit > 0 ? fmtNum(a.credits_limit) : '无限额' }}
            （{{ fmtPct(a.usage_pct) }}）
          </span>
        </div>
        <div class="an-flags">
          <GBadge
            v-for="f in a.flags"
            :key="f"
            :type="SEVERE_FLAGS.has(f) ? 'fail' : 'warn'"
          >
            {{ anomalyLabel(f) }}
          </GBadge>
        </div>
        <ul v-if="a.reasons.length" class="an-reasons">
          <li v-for="(r, i) in a.reasons" :key="i">{{ r }}</li>
        </ul>
      </div>
    </div>
    <template #footer>
      <GButton variant="primary" size="sm" @click="close">知道了</GButton>
    </template>
  </GModal>
</template>

<style scoped>
.an-list { display: flex; flex-direction: column; gap: 8px; }
.an-row {
  padding: 10px 12px;
  border-radius: var(--r-ctrl);
  background: var(--neutral-soft);
  border: 1px solid var(--glass-border);
}
.an-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
.an-masked { font-size: 12px; flex: none; max-width: 45%; }
.an-usage { font-size: 11px; color: var(--text-3); white-space: nowrap; }
.an-flags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
.an-reasons {
  margin-top: 7px;
  padding-left: 16px;
  font-size: 11.5px;
  color: var(--text-2);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
</style>
