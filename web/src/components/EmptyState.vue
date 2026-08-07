<script setup lang="ts">
import GIcon from './GIcon.vue'

/* EmptyState —— 空状态
   用法：
     <EmptyState title="暂无 Key" desc="添加 Tavily API Key 开始使用">
       <GButton variant="primary">添加 Key</GButton>
     </EmptyState> */
withDefaults(defineProps<{
  title: string
  desc?: string
  icon?: string
}>(), {
  icon: 'inbox',
})
</script>

<template>
  <div class="empty">
    <div class="empty-icon">
      <slot name="icon"><GIcon :name="icon" :size="26" :stroke-width="1.4" /></slot>
    </div>
    <p class="empty-title">{{ title }}</p>
    <p v-if="desc" class="empty-desc">{{ desc }}</p>
    <div v-if="$slots.default" class="empty-actions">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 44px 24px;
}
.empty-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 16px;
  color: var(--text-3);
  background: var(--neutral-soft);
  border: 1px solid var(--glass-border);
  margin-bottom: 14px;
}
.empty-title { font-size: 13.5px; font-weight: 600; color: var(--text-2); }
.empty-desc { margin-top: 4px; font-size: 12px; color: var(--text-3); max-width: 380px; }
.empty-actions { margin-top: 16px; display: flex; gap: 8px; }
</style>
