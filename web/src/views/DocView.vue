<script setup lang="ts">
/* DocView —— 面板内置 wiki 文档：左侧目录树 + 右侧 MdView 渲染
   目录 60s 轮询（docs/ 增删文档免重启可见）；内容按需加载不轮询。 */
import { computed, ref, watch } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import GlassCard from '@/components/GlassCard.vue'
import Skeleton from '@/components/Skeleton.vue'
import EmptyState from '@/components/EmptyState.vue'
import MdView from '@/components/MdView.vue'
import { usePolling } from '@/composables/usePolling'
import { getDocsTree, getDoc, type DocCategory, type WikiDoc } from '@/api/client'

const { data: treeResp } = usePolling(getDocsTree, { interval: 60000 })
const tree = computed<DocCategory[]>(() => treeResp.value?.tree ?? [])
const totalDocs = computed(() => tree.value.reduce((n, c) => n + c.docs.length, 0))

const activePath = ref('')
const doc = ref<WikiDoc | null>(null)
const loadingDoc = ref(false)
const docError = ref<string | null>(null)

// 默认选中第一篇文档
watch(
  tree,
  (t) => {
    if (!activePath.value && t.length && t[0].docs.length) {
      void selectDoc(t[0].docs[0].path)
    }
  },
  { immediate: true },
)

let docSeq = 0
async function selectDoc(path: string): Promise<void> {
  if (path === activePath.value) return
  activePath.value = path
  loadingDoc.value = true
  docError.value = null
  const seq = ++docSeq
  try {
    const r = await getDoc(path)
    if (seq === docSeq) doc.value = r.doc
  } catch (e) {
    if (seq === docSeq) {
      docError.value = e instanceof Error ? e.message : String(e)
      doc.value = null
    }
  } finally {
    if (seq === docSeq) loadingDoc.value = false
  }
}
</script>

<template>
  <div class="view docs-view">
    <PageHeader title="文档" :desc="`内置使用文档（wiki）· 共 ${totalDocs} 篇 · 支持 Markdown`" />
    <div class="docs-layout">
      <GlassCard pad="sm" class="docs-side">
        <div v-if="!tree.length" class="u-dim docs-empty">暂无文档</div>
        <div v-for="cat in tree" :key="cat.category" class="docs-cat">
          <div class="docs-cat-name">{{ cat.category }}</div>
          <button
            v-for="d in cat.docs"
            :key="d.path"
            type="button"
            class="docs-item"
            :class="{ active: activePath === d.path }"
            :title="d.path"
            @click="selectDoc(d.path)"
          >{{ d.title }}</button>
        </div>
      </GlassCard>

      <GlassCard class="docs-body">
        <Skeleton v-if="loadingDoc" :lines="6" />
        <EmptyState v-else-if="docError" icon="alert" title="文档加载失败" :desc="docError" />
        <template v-else-if="doc">
          <h2 class="docs-title">{{ doc.title }}</h2>
          <MdView :text="doc.content" />
        </template>
        <div v-else class="u-dim docs-empty">从左侧选择文档查看</div>
      </GlassCard>
    </div>
  </div>
</template>

<style scoped>
.docs-view { display: flex; flex-direction: column; }
.docs-layout {
  display: flex;
  gap: 12px;
  min-height: 0;
  flex: 1;
}

/* 左侧目录：固定宽，内部滚动 */
.docs-side {
  flex: none;
  width: 240px;
  overflow-y: auto;
  min-height: 0;
}
.docs-cat { margin-bottom: 10px; }
.docs-cat-name {
  padding: 2px 8px 5px;
  font-size: 11px;
  font-weight: 650;
  color: var(--text-3);
  letter-spacing: 0.03em;
}
.docs-item {
  display: block;
  width: 100%;
  padding: 6px 8px;
  margin-bottom: 2px;
  text-align: left;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-2);
  background: none;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  cursor: pointer;
  transition: background var(--dur-1) ease, color var(--dur-1) ease;
}
.docs-item:hover { background: var(--neutral-soft); color: var(--text); }
.docs-item.active {
  background: var(--accent-softer);
  border-color: var(--accent-soft);
  color: var(--accent-text);
}

/* 右侧内容：自适应滚动 */
.docs-body {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  min-height: 0;
}
.docs-title {
  margin: 0 0 12px;
  font-size: 18px;
  font-weight: 700;
  color: var(--text);
  padding-bottom: 10px;
  border-bottom: 1px solid var(--glass-border);
}
.docs-empty { padding: 24px; text-align: center; font-size: 12px; }
</style>
