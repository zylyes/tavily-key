<script setup lang="ts">
/* GlassCard —— 玻璃拟态卡片容器
   用法：
     <GlassCard title="用量趋势" desc="近 7 天" hover>
       <template #actions><GButton size="sm">导出</GButton></template>
       内容
       <template #footer>底部</template>
     </GlassCard> */
withDefaults(defineProps<{
  title?: string
  desc?: string
  pad?: 'none' | 'sm' | 'md' | 'lg'
  hover?: boolean
}>(), {
  pad: 'md',
  hover: false,
})
</script>

<template>
  <section class="glass-card" :class="[`pad-${pad}`, { hover }]">
    <header v-if="title || desc || $slots.actions" class="gc-head">
      <div class="gc-titles u-grow">
        <h3 v-if="title" class="gc-title">{{ title }}</h3>
        <p v-if="desc" class="gc-desc">{{ desc }}</p>
      </div>
      <div v-if="$slots.actions" class="gc-actions u-flex u-gap-2">
        <slot name="actions" />
      </div>
    </header>
    <div class="gc-body">
      <slot />
    </div>
    <footer v-if="$slots.footer" class="gc-foot">
      <slot name="footer" />
    </footer>
  </section>
</template>

<style scoped>
.glass-card {
  position: relative;
  background:
    linear-gradient(180deg, var(--glass-hi) 0%, transparent 140px),
    var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-card);
  transition: border-color var(--dur-2) var(--ease-out), box-shadow var(--dur-2) var(--ease-out),
    transform var(--dur-2) var(--ease-out);
}
.glass-card.hover:hover {
  border-color: var(--glass-border-strong);
  transform: translateY(-2px);
}

.pad-none .gc-body { padding: 0; }
.pad-sm .gc-body { padding: 12px 14px; }
.pad-md .gc-body { padding: 16px 18px; }
.pad-lg .gc-body { padding: 22px 24px; }
.pad-none .gc-head, .pad-sm .gc-head { padding: 12px 14px 0; }
.pad-md .gc-head, .pad-lg .gc-head { padding: 16px 18px 0; }

.gc-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.gc-title { color: var(--text); }
.gc-desc {
  margin-top: 3px;
  font-size: 11.5px;
  color: var(--text-3);
}
.gc-actions { flex: none; }

.gc-foot {
  padding: 12px 18px;
  border-top: 1px solid var(--glass-border);
}
</style>
