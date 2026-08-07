<script setup lang="ts">
/* SettingRow —— 设置项行：左侧标签 + 说明，右侧控件区（设置视图私有） */
defineProps<{
  label: string
  hint?: string
}>()
</script>

<template>
  <div class="setting-row">
    <div class="setting-row-text">
      <div class="setting-row-label">{{ label }}</div>
      <div v-if="hint" class="setting-row-hint">{{ hint }}</div>
    </div>
    <div class="setting-row-ctrl"><slot /></div>
  </div>
</template>

<style scoped>
.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 0;
}
.setting-row + .setting-row { border-top: 1px solid var(--glass-border); }

.setting-row-text { min-width: 0; }
.setting-row-label { font-size: 12.5px; font-weight: 550; }
.setting-row-hint {
  margin-top: 2px;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-3);
}

.setting-row-ctrl {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  width: 320px;
  max-width: 58%;
}
</style>
