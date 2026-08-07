<script setup lang="ts">
/* PageHeader —— 视图页头（标题 + 描述 + 操作区）
   用法：
     <PageHeader title="API Key 列表" desc="管理池中的 Tavily API Key">
       <template #actions>
         <GButton size="sm" @click="refresh">刷新</GButton>
         <GButton variant="primary" size="sm">添加 Key</GButton>
       </template>
     </PageHeader> */
defineProps<{
  title: string
  desc?: string
}>()
</script>

<template>
  <header class="page-header">
    <div class="ph-titles u-grow">
      <h1 class="ph-title">{{ title }}</h1>
      <p v-if="desc" class="ph-desc">{{ desc }}</p>
    </div>
    <div v-if="$slots.actions" class="ph-actions u-flex u-gap-2">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.ph-title { letter-spacing: .01em; }
.ph-desc {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-3);
}
.ph-actions { flex: none; }
</style>
